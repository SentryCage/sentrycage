import builtins
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


async def test_demo_blocks_network_credentials_dotenv_and_live_database(tmp_path, monkeypatch, capsys):
    import main
    monkeypatch.chdir(tmp_path)
    db = tmp_path/'live.db'
    db.write_bytes(b'existing database sentinel')
    monkeypatch.setenv('DB_PATH', str(db))
    monkeypatch.setenv('POLLING_INTERVAL', 'invalid')
    original_open = builtins.open
    original_getenv = os.getenv

    def guarded_open(file, *args, **kwargs):
        assert Path(file).name != '.env', 'Demo read .env'
        return original_open(file, *args, **kwargs)

    def guarded_getenv(name, *args):
        assert not any(part in name for part in ('KEY', 'TOKEN', 'RPC', 'CHAT_ID')), 'Demo read credentials'
        return original_getenv(name, *args)

    with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
         patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
         patch('dotenv.load_dotenv', side_effect=AssertionError('dotenv forbidden')), \
         patch('os.getenv', side_effect=guarded_getenv), \
         patch('builtins.open', side_effect=guarded_open), \
         patch('src.fetching.blockchain_client.BlockchainClient', side_effect=AssertionError('RPC constructed')), \
         patch('src.notifications.telegram_bot.Bot', side_effect=AssertionError('Telegram constructed')), \
         patch('src.reasoning.llm_factory.LLMFactory.create_provider', side_effect=AssertionError('LLM constructed')):
        assert await main.main(['--demo']) == 0
    output = capsys.readouterr().out
    assert 'DEMO — dati sintetici' in output
    assert 'EVENT:' in output and 'SIGNAL: accumulation' in output and 'ALERT' in output
    assert 'SQLITE: 1 signal saved and read back' in output
    assert db.read_bytes() == b'existing database sentinel'
    assert list(tmp_path.iterdir()) == [db]


def test_demo_in_fresh_process_without_environment(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT/'main.py'), '--demo'], cwd=tmp_path,
                            env={'PATH': os.environ['PATH'], 'PYTHONUTF8': '1'},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'SIGNAL: accumulation' in result.stdout
    assert not list(tmp_path.iterdir())
