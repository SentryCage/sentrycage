"""Shared raw transaction → analysis → signal → storage → recipient pipeline."""
from dataclasses import dataclass, field
from src.analysis.transaction_analyzer import TransactionAnalyzer
from src.signals.signal_generator import SignalGenerator
from src.signals.enhanced_signal import create_signal_from_analysis
from src.database import sqlite


class DeliveryError(RuntimeError):
    pass


class SQLiteStorage:
    def __init__(self, db_path):
        self.db_path = db_path
        sqlite.init_db(db_path)

    def is_processed(self, tx_hash, chain):
        return sqlite.is_tx_processed(tx_hash, chain, self.db_path)

    def mark_processed(self, tx_hash, chain, address=None):
        sqlite.mark_tx_processed(tx_hash, chain, address, self.db_path)

    def save(self, signal):
        data = signal.to_dict()
        data['source'] = 'ai_reasoning' if hasattr(signal, 'reasoning_chain') else 'whale_tracker'
        data['metadata'] = data.copy()
        return sqlite.save_signal(data, self.db_path)

    def read(self):
        return sqlite.get_signals(db_path=self.db_path)


@dataclass
class PipelineResult:
    events: int = 0
    signals: list = field(default_factory=list)
    deliveries: list = field(default_factory=list)


class TransactionPipeline:
    def __init__(self, storage, recipient, *, threshold=100, analyzer=None,
                 generator=None, reasoning_agent=None):
        self.storage = storage
        self.recipient = recipient
        self.analyzer = analyzer if analyzer is not None else TransactionAnalyzer(threshold=threshold)
        self.generator = generator if generator is not None else SignalGenerator()
        self.reasoning_agent = reasoning_agent

    async def process(self, transactions, chain, wallet_address=None):
        result = PipelineResult()
        analyzed = []
        for raw in transactions:
            tx = dict(raw, chain=chain)
            tx_hash = tx.get('hash')
            if not tx_hash:
                raise ValueError('Transaction is missing hash')
            if self.storage.is_processed(tx_hash, chain):
                continue
            # Limite noto: marcatura anticipata; non garantisce recupero dopo crash.
            self.storage.mark_processed(tx_hash, chain, wallet_address)
            event = self.analyzer.analyze_transaction(tx)
            if event.get('error'):
                raise ValueError('Transaction analysis failed')
            result.events += 1
            if event.get('is_significant'):
                analyzed.append(event)
        return await self.process_analyzed(analyzed, result)

    async def process_analyzed(self, transactions, result=None):
        result = result if result is not None else PipelineResult(events=len(transactions))
        if self.reasoning_agent is not None and transactions:
            data = await self.reasoning_agent.analyze_transactions(transactions, use_reasoning=True)
            signals = [create_signal_from_analysis(s, enable_reasoning=True) for s in data]
        else:
            signals = [s for tx in transactions for s in self.generator.generate_signals(tx)]
        for signal in signals:
            self.storage.save(signal)
            delivery = await self.recipient.send_signal(signal)
            if delivery is not True and getattr(delivery, 'value', None) not in ('sent', 'disabled'):
                raise DeliveryError('Signal saved, but notification delivery failed')
            result.signals.append(signal)
            result.deliveries.append(delivery)
        return result
