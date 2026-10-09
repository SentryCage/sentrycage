from unittest.mock import AsyncMock, Mock, patch
import pytest
from src.orchestration.doctor import run_doctor
from tests.integration.test_runtime_pipeline import settings


async def test_doctor_checks_enabled_services_and_never_sends_or_uses_llm(tmp_path, capsys):
    config = settings(tmp_path)
    blockchain = Mock()
    blockchain.get_latest_block_number.return_value = 100
    blockchain.get_chain_id.return_value = 1
    explorer = Mock()
    explorer._make_request.return_value = {'result': '0x64'}
    notifier = AsyncMock()
    with patch('src.reasoning.llm_factory.LLMFactory.create_provider', side_effect=AssertionError('LLM forbidden')):
        assert await run_doctor(config, blockchain=blockchain, explorer=explorer, notifier=notifier) == 0
    notifier.send_signal.assert_not_awaited()
    notifier.bot.send_message.assert_not_awaited()
    assert not (tmp_path/'live.db').exists()
    assert 'operational' in capsys.readouterr().out


async def test_doctor_reports_wrong_rpc_chain_and_explorer_failure(tmp_path, capsys):
    blockchain = Mock()
    blockchain.get_chain_id.return_value = 42161
    explorer = Mock()
    explorer._make_request.return_value = {'result': 'invalid'}
    assert await run_doctor(settings(tmp_path), blockchain=blockchain, explorer=explorer, notifier=AsyncMock()) == 3
    output = capsys.readouterr().out
    assert 'RPC ethereum: FAILED' in output and 'EXPLORER ethereum: FAILED' in output


@pytest.mark.parametrize('events,code', [(0, 0), (2, 10)])
async def test_once_runs_one_scan_and_closes(tmp_path, monkeypatch, events, code):
    import main
    monkeypatch.setattr('dotenv.load_dotenv', Mock())
    config = settings(tmp_path)
    tracker = AsyncMock()
    tracker.process_new_transactions.return_value = events
    with patch('src.config.settings.Settings.from_env', return_value=config), patch('main.WhaleTracker', return_value=tracker):
        assert await main.main(['--once']) == code
    tracker.initialize.assert_awaited_once()
    tracker.process_new_transactions.assert_awaited_once()
    tracker.close.assert_awaited_once()


async def test_once_failure_has_exit_code_and_closes(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr('dotenv.load_dotenv', Mock())
    tracker = AsyncMock()
    tracker.process_new_transactions.side_effect = TimeoutError('secret-url')
    with patch('src.config.settings.Settings.from_env', return_value=settings(tmp_path)), patch('main.WhaleTracker', return_value=tracker):
        assert await main.main(['--once']) == 3
    tracker.close.assert_awaited_once()


async def test_doctor_verifies_chat_and_local_model_without_inference(tmp_path):
    from src.config.settings import Settings
    from src.notifications.telegram_bot import TelegramNotifier
    config = Settings.from_env(dict(ETHEREUM_RPC_URL='https://rpc.invalid', ETHERSCAN_API_KEY='offline-key',
                                   TELEGRAM_BOT_TOKEN='123:offline', TELEGRAM_CHAT_ID='-100123',
                                   ENABLE_REASONING='true', LLM_PROVIDER='ollama',
                                   OLLAMA_URL='http://localhost:11434'))
    rpc = Mock()
    rpc.get_chain_id.return_value = 1
    rpc.get_latest_block_number.return_value = 100
    explorer = Mock()
    explorer._make_request.return_value = {'result': '0x64'}
    bot = AsyncMock()
    notifier = TelegramNotifier(token='123:offline', chat_id=-100123, bot=bot)
    session = Mock()
    session.get.return_value.json.return_value = {'models': [{'name': 'llama3.1:8b'}]}
    with patch('src.reasoning.llm_factory.LLMFactory.create_provider', side_effect=AssertionError('inference forbidden')):
        assert await run_doctor(config, blockchain=rpc, explorer=explorer, notifier=notifier, session=session) == 0
    bot.get_chat.assert_awaited_once_with(-100123)
    bot.send_message.assert_not_awaited()
    assert session.get.call_args.kwargs['timeout'] == config.request_timeout
    session.get.return_value.json.return_value = {'models': []}
    assert await run_doctor(config, blockchain=rpc, explorer=explorer, notifier=notifier, session=session) == 3


async def test_doctor_cloud_ai_is_explicitly_unverified_without_requests(tmp_path, capsys):
    from dataclasses import replace
    config = replace(settings(tmp_path), enable_reasoning=True,
                     llm_env={'ENABLE_REASONING': 'true', 'LLM_PROVIDER': 'claude', 'CLAUDE_API_KEY': 'offline-key'})
    rpc = Mock()
    rpc.get_chain_id.return_value = 1
    rpc.get_latest_block_number.return_value = 100
    explorer = Mock()
    explorer._make_request.return_value = {'result': '0x64'}
    session = Mock()
    assert await run_doctor(config, blockchain=rpc, explorer=explorer, notifier=AsyncMock(), session=session) == 3
    session.get.assert_not_called()
    assert 'UNVERIFIED' in capsys.readouterr().out
