from unittest.mock import AsyncMock, Mock, patch
import pytest
from src.config.settings import Settings, ConfigurationError
from src.config.wallet_registry import Chain, ALL_WALLETS
from src.orchestration.pipeline import TransactionPipeline, SQLiteStorage


def raw_transaction():
    return {'hash': '0x' + 'ab' * 32, 'from': '0x' + '11' * 20,
            'to': ALL_WALLETS[1].address, 'value': str(500 * 10**18),
            'input': '0x', 'timeStamp': '1791547200', 'isError': '0'}


def settings(tmp_path):
    return Settings.from_env(dict(ETHEREUM_RPC_URL='https://rpc.invalid',
                                   ETHERSCAN_API_KEY='offline-key', NOTIFICATIONS_ENABLED='false',
                                   DB_PATH=str(tmp_path/'live.db')))


async def test_raw_to_real_analysis_signal_and_storage(tmp_path):
    storage = SQLiteStorage(str(tmp_path/'signals.db'))
    recipient = AsyncMock()
    recipient.send_signal.return_value = True
    pipeline = TransactionPipeline(storage, recipient)
    result = await pipeline.process([raw_transaction()], 'ethereum')
    assert result.events == 1
    assert result.signals[0].signal_type.value == 'accumulation'
    row = storage.read()[0]
    assert row['transaction_hash'] == raw_transaction()['hash']
    assert row['wallet_address'] == ALL_WALLETS[1].address
    assert row['confidence'] == .9
    assert row['metadata']['description'] == result.signals[0].description
    recipient.send_signal.assert_awaited_once_with(result.signals[0])
    duplicate = await pipeline.process([raw_transaction()], 'ethereum')
    assert duplicate.events == 0


def test_ai_off_never_constructs_provider(tmp_path):
    from main import WhaleTracker
    with patch('src.reasoning.llm_factory.LLMFactory.create_provider', side_effect=AssertionError('AI contacted')) as factory:
        tracker = WhaleTracker(settings(tmp_path), blockchain_client=Mock(), explorer_client=Mock(), recipient=AsyncMock())
    assert tracker.reasoning_agent is None
    factory.assert_not_called()


def test_invalid_configuration_has_no_client_or_db_side_effects(monkeypatch):
    from main import WhaleTracker
    monkeypatch.delenv('ETHEREUM_RPC_URL', raising=False)
    with patch('src.fetching.blockchain_client.BlockchainClient') as client, patch('src.orchestration.pipeline.SQLiteStorage') as storage:
        with pytest.raises(ConfigurationError, match='ETHEREUM_RPC_URL'):
            WhaleTracker()
        client.assert_not_called()
        storage.assert_not_called()


async def test_normal_tracker_delivers_to_actual_send_message_mock(tmp_path):
    from main import WhaleTracker
    from src.notifications.telegram_bot import TelegramNotifier
    env = dict(ETHEREUM_RPC_URL='https://rpc.invalid', ETHERSCAN_API_KEY='offline-key',
               TELEGRAM_BOT_TOKEN='123:offline', TELEGRAM_CHAT_ID='-100123',
               DB_PATH=str(tmp_path/'live.db'))
    config = Settings.from_env(env)
    bot = AsyncMock()
    recipient = TelegramNotifier(token=config.telegram_token, chat_id=config.telegram_chat_id, bot=bot)
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = [raw_transaction()]
    tracker = WhaleTracker(config, blockchain_client=Mock(), explorer_client=explorer, recipient=recipient)
    tracker.blockchain_client.get_chain_id.return_value = 1
    tracker.blockchain_client.get_latest_block_number.return_value = 120
    await tracker.initialize()
    await tracker.process_wallet_transactions(ALL_WALLETS[1].address, Chain.ETHEREUM, 10, 20)
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs['chat_id'] == -100123
    assert '500.00 ETH' in kwargs['text']
    assert len(tracker.storage.read()) == 1


async def test_delivery_failure_reaches_coordinator_and_is_not_retried_as_fallback(tmp_path):
    from main import WhaleTracker
    from src.notifications.telegram_bot import TelegramNotifier
    from src.orchestration.pipeline import DeliveryError
    from telegram.error import TelegramError
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramError('offline failure')
    recipient = TelegramNotifier(token='123:offline', chat_id=-100123, bot=bot)
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = [raw_transaction()]
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=Mock(), explorer_client=explorer, recipient=recipient)
    with pytest.raises(DeliveryError):
        await tracker.process_wallet_transactions(ALL_WALLETS[1].address, Chain.ETHEREUM, 10, 20)
    assert len(tracker.storage.read()) == 1
    bot.send_message.assert_awaited_once()
    # Known recovery limitation is documented: early marking survives failure.
    assert tracker.storage.is_processed(raw_transaction()['hash'], 'ethereum')


def test_storage_constraint_failure_is_not_silently_discarded(tmp_path):
    import sqlite3
    from src.database.sqlite import save_signal
    path = str(tmp_path/'signals.db')
    SQLiteStorage(path)
    with pytest.raises(sqlite3.IntegrityError):
        save_signal(dict(signal_type='transfer', source='invalid_source'), path)


async def test_empty_successful_scan_advances_cursor(tmp_path):
    from main import WhaleTracker
    from src.notifications.telegram_bot import TelegramNotifier
    rpc = Mock()
    rpc.get_latest_block_number.return_value = 120
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = []
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=rpc, explorer_client=explorer,
                           recipient=TelegramNotifier(token='', enabled=False))
    tracker.last_processed_blocks[Chain.ETHEREUM] = 100
    assert await tracker.process_new_transactions() == 0
    assert tracker.last_processed_blocks[Chain.ETHEREUM] == 120


async def test_failed_onchain_transaction_generates_no_alert(tmp_path):
    recipient = AsyncMock()
    raw = dict(raw_transaction(), isError='1')
    pipeline = TransactionPipeline(SQLiteStorage(str(tmp_path/'signals.db')), recipient)
    result = await pipeline.process([raw], 'ethereum')
    assert result.events == 1 and not result.signals
    recipient.send_signal.assert_not_awaited()
