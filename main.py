"""SentryCage CLI and live acquisition coordinator."""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)


class WhaleTracker:
    def __init__(self, settings=None, *, blockchain_client=None, explorer_client=None,
                 storage=None, recipient=None):
        from src.config.settings import Settings
        self.settings = settings if settings is not None else Settings.from_env()
        from src.fetching.blockchain_client import BlockchainClient
        from src.fetching.explorer_api import ExplorerAPIClient
        from src.notifications.telegram_bot import TelegramNotifier
        from src.orchestration.pipeline import TransactionPipeline, SQLiteStorage
        self.blockchain_client = blockchain_client if blockchain_client is not None else BlockchainClient(self.settings)
        self.explorer_client = explorer_client if explorer_client is not None else ExplorerAPIClient(
            self.settings.etherscan_api_key, self.settings.request_timeout)
        self.telegram_notifier = recipient if recipient is not None else TelegramNotifier(
            token=self.settings.telegram_token, chat_id=self.settings.telegram_chat_id,
            enabled=self.settings.notifications_enabled, timeout=self.settings.request_timeout)
        self.reasoning_agent = None
        self.llm_config = None
        if self.settings.enable_reasoning:
            from src.reasoning.agent_orchestrator import ReasoningAgentOrchestrator
            from src.config.llm_config import LLMConfig
            self.llm_config = LLMConfig(self.settings.llm_env)
            self.reasoning_agent = ReasoningAgentOrchestrator(self.llm_config)
        self.storage = storage if storage is not None else SQLiteStorage(self.settings.db_path)
        self.pipeline = TransactionPipeline(self.storage, self.telegram_notifier,
                                            threshold=self.settings.transaction_threshold,
                                            reasoning_agent=self.reasoning_agent)
        self.transaction_analyzer = self.pipeline.analyzer
        self.signal_generator = self.pipeline.generator
        self.last_processed_blocks = {}

    async def initialize(self):
        await self.telegram_notifier.initialize()
        for chain in self.settings.enabled_chains:
            from src.fetching.explorer_api import ExplorerAPIClient
            if self.blockchain_client.get_chain_id(chain) != ExplorerAPIClient.CHAIN_IDS[chain]:
                raise ConnectionError('RPC chain ID does not match enabled chain')
            latest = self.blockchain_client.get_latest_block_number(chain)
            self.last_processed_blocks[chain] = max(0, latest - 100)
        self._log_startup_banner()

    def _log_startup_banner(self):
        from src.config.wallet_registry import ALL_WALLETS
        logger.info('SentryCage: %d static wallets; enabled chains: %s; AI: %s; Telegram: %s',
                    len(ALL_WALLETS), ', '.join(c.value for c in self.settings.enabled_chains),
                    ('configured; provider available, readiness unverified' if self.reasoning_agent.llm else 'configured; fallback active') if self.reasoning_agent else 'off',
                    getattr(self.telegram_notifier, 'status', 'not verified'))

    async def process_new_transactions(self):
        from src.config.wallet_registry import ALL_WALLETS
        events = 0
        for chain in self.settings.enabled_chains:
            current = self.blockchain_client.get_latest_block_number(chain)
            last = self.last_processed_blocks.get(chain, 0)
            if current <= last:
                continue
            for wallet in ALL_WALLETS:
                if chain in wallet.chains:
                    result = await self.process_wallet_transactions(wallet.address, chain, last + 1, current)
                    events += result.events
            self.last_processed_blocks[chain] = current
        return events

    async def process_wallet_transactions(self, address, chain, from_block, to_block):
        transactions = self.explorer_client.get_wallet_transactions(
            address=address, chain=chain, start_block=from_block, end_block=to_block)
        if len(transactions) >= 100:
            from src.fetching.explorer_api import IncompleteExplorerScan
            raise IncompleteExplorerScan('Explorer page full; pagination requires follow-up; cursor retained')
        return await self.pipeline.process(transactions, chain.value, address)

    async def _process_transaction_batch(self, transactions):
        return await self.pipeline.process_analyzed(transactions)

    async def run_forever(self):
        await self.initialize()
        while True:
            try:
                events = await self.process_new_transactions()
                logger.info('Scan complete: %d new events', events)
            except Exception as error:
                # Non stampare eccezioni dei provider: possono contenere URL con credenziali.
                logger.error('Scan failed (%s); cursor retained for incomplete chain', type(error).__name__)
            await asyncio.sleep(self.settings.polling_interval)

    async def close(self):
        if self.reasoning_agent is not None:
            await self.reasoning_agent.close()
        close = getattr(self.telegram_notifier, 'close', None)
        if close:
            await close()
        session = getattr(self.explorer_client, 'session', None)
        if session:
            session.close()


async def main(argv=None):
    parser = argparse.ArgumentParser(description='SentryCage — on-chain wallet monitoring')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--demo', action='store_true', help='Offline demo with synthetic data; no credentials')
    modes.add_argument('--doctor', action='store_true', help='Read-only checks; no Telegram messages or AI inference')
    modes.add_argument('--once', action='store_true', help='Run one scan: exit 0 empty, 10 events, 2 config error, 3 failure')
    modes.add_argument('--test', action='store_true', help='Send one REAL Telegram message with synthetic data')
    modes.add_argument('--preview', action='store_true', help='Send REAL synthetic Telegram sample messages')
    args = parser.parse_args(argv)
    if args.demo:
        from src.orchestration.demo import run_demo
        return await run_demo()
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    from src.config.settings import Settings, ConfigurationError
    try:
        if args.preview:
            return await run_preview()
        if args.test:
            return await run_test()
        settings = Settings.from_env()
        from src.orchestration.logging_setup import configure_logging
        configure_logging(settings)
        if args.doctor:
            from src.orchestration.doctor import run_doctor
            return await run_doctor(settings)
        tracker = WhaleTracker(settings)
        try:
            if args.once:
                await tracker.initialize()
                events = await tracker.process_new_transactions()
                print(f'SCAN: success; {events} new events')
                return 10 if events else 0
            await tracker.run_forever()
            return 0
        finally:
            await tracker.close()
    except ConfigurationError as error:
        print(f'CONFIG ERROR: {error}')
        return 2
    except Exception as error:
        print(f'FAILED ({type(error).__name__}); run --doctor and check service configuration/connectivity')
        return 3


def _sample_signals() -> List:
    """Build a representative gallery of alerts covering each signal type + AI reasoning."""
    from src.signals.signal_generator import Signal, SignalType, SignalStrength
    from src.signals.enhanced_signal import EnhancedSignal
    now = datetime.now()
    samples: List[Signal] = [
        Signal(
            signal_type=SignalType.ACCUMULATION,
            strength=SignalStrength.VERY_HIGH,
            transaction_hash="0x" + "ab" * 32,
            wallet_address="0x" + "cd" * 20,
            wallet_name="Whale #1",
            wallet_category="whale",
            chain="ethereum",
            value_eth=1234.5,
            description="Whale wallet accumulating 1234.50 ETH over 3 transactions",
            timestamp=now,
            confidence=0.9,
        ),
        Signal(
            signal_type=SignalType.DISTRIBUTION,
            strength=SignalStrength.HIGH,
            transaction_hash="0x" + "ef" * 32,
            wallet_address="0x" + "12" * 20,
            wallet_name=None,
            wallet_category="market_maker",
            chain="arbitrum",
            value_eth=800.0,
            description="Market maker distributing 800.00 ETH to multiple addresses",
            timestamp=now,
            confidence=0.75,
        ),
        EnhancedSignal(
            signal_type=SignalType.EXCHANGE_DEPOSIT,
            strength=SignalStrength.HIGH,
            transaction_hash="0x" + "11" * 32,
            wallet_address="0x" + "22" * 20,
            wallet_name="Smart Money",
            wallet_category="whale",
            chain="ethereum",
            value_eth=500.0,
            description="Large deposit to Binance — possible sell pressure",
            reasoning_chain=[
                "Wallet moved 500 ETH to a known Binance hot wallet",
                "No matching withdrawal in the last 24h",
            ],
            predicted_impact="bearish",
            recommended_actions=[
                "Watch ETH order book depth on major venues",
                "Check for follow-on deposits from related wallets",
            ],
            correlations=["2 other MM wallets deposited to CEX in the last hour"],
            timestamp=now,
            confidence=0.82,
        ),
    ]
    return samples


async def run_preview():
    """Send real synthetic alerts to the explicitly configured destination."""
    return await send_samples(preview=True)


async def run_test():
    """Send one real synthetic alert; no RPC, explorer or LLM initialization."""
    return await send_samples(preview=False)


async def send_samples(preview=False):
    from src.config.settings import required, chat_id
    token = required(os.getenv('TELEGRAM_BOT_TOKEN', ''), 'TELEGRAM_BOT_TOKEN')
    destination = chat_id(os.getenv('TEST_CHAT_ID') or os.getenv('TELEGRAM_CHAT_ID'))
    from src.notifications.telegram_bot import TelegramNotifier
    from src.orchestration.logging_setup import configure_logging
    configure_logging(secrets=[token])
    notifier = TelegramNotifier(token=token, chat_id=destination, enabled=True)
    logger.warning('Sending REAL Telegram messages with synthetic data to configured test destination')
    try:
        await notifier.initialize()
        for signal in _sample_signals() if preview else _sample_signals()[:1]:
            signal.description = 'SYNTHETIC TEST — ' + signal.description
            outcome = await notifier.send_signal(signal)
            if not outcome:
                raise ConnectionError('Telegram test delivery failed')
        return 0
    finally:
        await notifier.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
