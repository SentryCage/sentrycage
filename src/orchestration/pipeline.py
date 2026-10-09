"""Shared raw transaction → analysis → signal → storage → recipient pipeline."""
from dataclasses import dataclass, field
from src.analysis.transaction_analyzer import TransactionAnalyzer
from src.signals.signal_generator import SignalGenerator
from src.signals.enhanced_signal import create_signal_from_analysis
from src.database import sqlite
from src.database.recovery import RecoveryStore
import json
import asyncio
import time


class DeliveryError(RuntimeError):
    pass


class SQLiteStorage(RecoveryStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        from src.config.runtime_wallets import WalletRegistry
        self.wallet_registry = WalletRegistry(db_path)

    def is_processed(self, tx_hash, chain):
        return sqlite.is_tx_processed(tx_hash, chain, self.db_path)

    def mark_processed(self, tx_hash, chain, address=None):
        sqlite.mark_tx_processed(tx_hash, chain, address, self.db_path)

    def save(self, signal):
        data = signal.to_dict()
        data['source'] = 'ai_reasoning' if data.get('provenance', {}).get('origin') == 'ai' else 'whale_tracker'
        data['metadata'] = data.copy()
        return sqlite.save_signal(data, self.db_path)

    def read(self):
        return sqlite.get_signals(db_path=self.db_path)


@dataclass
class PipelineResult:
    events: int = 0
    signals: list = field(default_factory=list)
    deliveries: list = field(default_factory=list)


class StoredSignal:
    """An immutable delivery snapshot; never regenerate an alert during retry."""
    def __init__(self, row):
        self.payload = json.loads(row['payload'])
        self.message = row['message']

    def get_message(self):
        return self.message

    def to_dict(self):
        return self.payload.copy()


class TransactionPipeline:
    def __init__(self, storage, recipient, *, threshold=100, analyzer=None,
                 generator=None, reasoning_agent=None, clock=time.time, delivery_timeout=10,
                 retry_base=5, enabled_chains=None):
        self.storage = storage
        self.recipient = recipient
        self.analyzer = analyzer if analyzer is not None else TransactionAnalyzer(threshold=threshold, registry=storage.wallet_registry)
        self.generator = generator if generator is not None else SignalGenerator()
        self.reasoning_agent = reasoning_agent
        if reasoning_agent is not None:
            reasoning_agent.fallback_analyzer = self.analyzer
        self.clock = clock
        self.delivery_timeout = delivery_timeout
        self.lease_seconds = max(60, delivery_timeout + 30)
        self.retry_base = retry_base
        self.enabled_chains = enabled_chains

    @property
    def route(self):
        key = getattr(self.recipient, 'delivery_key', None)
        return key if isinstance(key, str) else 'injected'

    async def process(self, transactions, chain, wallet_address=None, *, deliver=True):
        result = PipelineResult()
        for raw in transactions:
            staged = self.storage.stage(raw, chain, wallet_address)
            if staged is None:
                continue
            tx_hash = staged['hash']
            try:
                event = self.analyzer.analyze_transaction(dict(staged, chain=chain))
                if event.get('error'):
                    raise ValueError('Transaction analysis failed')
                signals = await self.generate([event]) if event.get('is_significant') else []
                if self.storage.complete(tx_hash, chain, signals, self.route):
                    result.events += 1
                    result.signals.extend(signals)
            except Exception as error:
                self.storage.record_error(tx_hash, chain, error)
                raise
        if deliver:
            result.deliveries.extend(await self.flush_notifications())
        return result

    async def recover(self, chains=None, *, deliver=True):
        result = PipelineResult()
        for work in self.storage.pending_work(chains):
            recovered = await self.process([json.loads(work['raw_json'])], work['chain'], work['wallet_address'], deliver=deliver)
            result.events += recovered.events
            result.signals.extend(recovered.signals)
            result.deliveries.extend(recovered.deliveries)
        return result

    async def process_analyzed(self, transactions, result=None):
        # Compatibility entry point uses the same journal and completion semantics.
        result = result if result is not None else PipelineResult()
        for event in transactions:
            chain = event.get('chain', 'ethereum')
            if self.storage.stage(event, chain) is None:
                continue
            signals = await self.generate([event])
            if self.storage.complete(event['hash'], chain, signals, self.route):
                result.events += 1
                result.signals.extend(signals)
        result.deliveries.extend(await self.flush_notifications())
        return result

    async def generate(self, transactions):
        if self.reasoning_agent is not None and transactions:
            data = await self.reasoning_agent.analyze_transactions(transactions, use_reasoning=True)
            references = {(tx['chain'], tx['hash']) for tx in transactions}
            if any((signal.get('chain'), signal.get('transaction_hash')) not in references for signal in data):
                raise ValueError('Reasoning result references a different transaction')
            return [create_signal_from_analysis(s, enable_reasoning=True) for s in data]
        signals = [s for tx in transactions for s in self.generator.generate_signals(tx)]
        for signal in signals:
            signal.provenance = {'schema_version': 1, 'origin': 'rules', 'provider': None, 'model': None,
                                 'provider_fallback': False, 'analysis_fallback': False,
                                 'fallback_reason': None, 'attribution': 'rule_input'}
        return signals

    async def flush_notifications(self):
        deliveries = []
        route = self.route
        if route == 'disabled':
            return deliveries  # Existing notifications stay paused, not silently acknowledged.
        for _ in range(100):
            row = self.storage.claim_notification(route, self.clock(), self.lease_seconds, self.enabled_chains)
            if row is None:
                break
            error_name = None
            try:
                if route != self.route:
                    raise DeliveryError('Recipient changed during delivery')
                delivery = await asyncio.wait_for(self.recipient.send_signal(StoredSignal(row)), self.delivery_timeout)
                if delivery is not True and getattr(delivery, 'value', None) != 'sent':
                    raise DeliveryError('Notification was not sent')
            except Exception as error:
                error_name = type(error).__name__
            delay = min(3600, self.retry_base * 2 ** min(row['attempts'] - 1, 10))
            if not self.storage.record_delivery(row['id'], row['owner'],
                                                'pending' if error_name else 'sent', error_name,
                                                self.clock() + delay if error_name else 0):
                raise DeliveryError('Notification lease lost; receipt was not acknowledged')
            if not error_name:
                deliveries.append(delivery)
        if self.storage.outstanding_notifications(self.enabled_chains):
            raise DeliveryError('Notifications remain pending: retry scheduled, lease active or recipient changed')
        return deliveries
