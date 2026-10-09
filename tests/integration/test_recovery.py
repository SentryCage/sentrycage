"""Real SQLite crash boundaries, rollback, replay and checkpoint restart tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, Mock
import pytest
from src.database.sqlite import get_connection
from src.orchestration.pipeline import TransactionPipeline, SQLiteStorage, DeliveryError
from tests.integration.test_runtime_pipeline import raw_transaction, settings


def recipient():
    target = AsyncMock()
    target.delivery_key = 'injected'
    target.send_signal.return_value = True
    return target


async def test_failed_analysis_is_recovered_from_persisted_raw_after_restart(tmp_path):
    path = str(tmp_path/'recovery.db')
    storage = SQLiteStorage(path)
    analyzer = Mock()
    analyzer.analyze_transaction.side_effect = RuntimeError('private diagnostic')
    pipeline = TransactionPipeline(storage, recipient(), analyzer=analyzer)
    with pytest.raises(RuntimeError):
        await pipeline.process([raw_transaction()], 'ethereum')
    assert not storage.is_processed(raw_transaction()['hash'], 'ethereum')
    work = storage.pending_work()[0]
    assert work['last_error'] == 'RuntimeError'
    assert json.loads(work['raw_json']) == raw_transaction()
    resumed = TransactionPipeline(SQLiteStorage(path), recipient())
    result = await resumed.recover(['ethereum'])
    assert result.events == 1 and len(resumed.storage.read()) == 1
    assert not resumed.storage.pending_work()
    assert (await resumed.recover(['ethereum'])).events == 0


async def test_database_failure_rolls_back_signals_intent_and_processed_marker(tmp_path):
    path = str(tmp_path/'rollback.db')
    storage = SQLiteStorage(path)
    target = recipient()
    with get_connection(path) as con:
        con.execute("""CREATE TRIGGER fail_intent BEFORE INSERT ON notification_outbox
                       BEGIN SELECT RAISE(ABORT, 'injected crash boundary'); END""")
    pipeline = TransactionPipeline(storage, target)
    with pytest.raises(Exception, match='injected crash boundary'):
        await pipeline.process([raw_transaction()], 'ethereum')
    assert storage.read() == []
    assert storage.pending_notifications('injected') == []
    assert not storage.is_processed(raw_transaction()['hash'], 'ethereum')
    assert len(storage.pending_work()) == 1
    target.send_signal.assert_not_awaited()
    with get_connection(path) as con:
        con.execute('DROP TRIGGER fail_intent')
    resumed = TransactionPipeline(SQLiteStorage(path), recipient())
    assert (await resumed.recover()).events == 1
    assert len(resumed.storage.read()) == 1


async def test_notification_error_preserves_local_completion_and_exact_message(tmp_path):
    path = str(tmp_path/'notification.db')
    storage = SQLiteStorage(path)
    target = recipient()
    target.send_signal.side_effect = TimeoutError('private URL')
    with pytest.raises(DeliveryError):
        await TransactionPipeline(storage, target).process([raw_transaction()], 'ethereum')
    assert storage.is_processed(raw_transaction()['hash'], 'ethereum')
    pending = storage.pending_notifications('injected')[0]
    first_message = target.send_signal.await_args.args[0].get_message()
    resumed_target = recipient()
    import time
    resumed = TransactionPipeline(SQLiteStorage(path), resumed_target, clock=lambda: time.time() + 10)
    result = await resumed.process([raw_transaction()], 'ethereum')
    assert result.events == 0 and len(resumed.storage.read()) == 1
    assert resumed_target.send_signal.await_args.args[0].get_message() == first_message == pending['message']
    assert not resumed.storage.pending_notifications('injected')


async def test_hard_process_exit_after_staging_can_be_recovered(tmp_path):
    path = str(tmp_path/'hard-exit.db')
    root = Path(__file__).resolve().parents[2]
    program = '''import json, os, sys
from src.orchestration.pipeline import SQLiteStorage
storage = SQLiteStorage(sys.argv[1])
storage.stage(json.loads(sys.argv[2]), 'ethereum')
os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', program, path, json.dumps(raw_transaction())],
                            cwd=root, env={'PATH': os.environ['PATH'], 'PYTHONUTF8': '1'}, timeout=20)
    assert result.returncode == 17
    resumed = TransactionPipeline(SQLiteStorage(path), recipient())
    assert (await resumed.recover()).events == 1
    assert len(resumed.storage.read()) == 1


async def test_tracker_checkpoint_survives_restart_and_empty_scan(tmp_path):
    from main import WhaleTracker
    rpc = Mock()
    rpc.get_chain_id.return_value = 1
    rpc.get_latest_block_number.return_value = 1000
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = []
    config = settings(tmp_path)
    tracker = WhaleTracker(config, blockchain_client=rpc, explorer_client=explorer, recipient=recipient())
    await tracker.initialize()
    assert await tracker.process_new_transactions() == 0
    restarted = WhaleTracker(config, blockchain_client=rpc, explorer_client=explorer, recipient=recipient())
    rpc.get_latest_block_number.return_value = 2000
    await restarted.initialize()
    from src.config.wallet_registry import Chain
    assert restarted.last_processed_blocks[Chain.ETHEREUM] == 1000
    await restarted.process_new_transactions()
    assert explorer.get_wallet_transactions.call_args.kwargs['start_block'] == 1001
    assert restarted.storage.load_checkpoint('ethereum') == 2000


def test_upgrade_preserves_legacy_records_without_inventing_delivery(tmp_path):
    import sqlite3
    path = str(tmp_path/'legacy.db')
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE processed_transactions(tx_hash TEXT, chain TEXT, wallet_address TEXT, '
                    'processed_at TEXT, PRIMARY KEY(tx_hash, chain))')
        con.execute("INSERT INTO processed_transactions VALUES ('legacy', 'ethereum', NULL, '2020')")
    storage = SQLiteStorage(path)
    assert storage.is_processed('legacy', 'ethereum')
    assert storage.pending_work() == storage.pending_notifications('injected') == []


def test_concurrent_local_completion_commits_one_signal_and_intent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from src.analysis.transaction_analyzer import TransactionAnalyzer
    from src.signals.signal_generator import SignalGenerator
    from src.database.sqlite import count_signals
    path = str(tmp_path/'concurrent.db')
    stores = [SQLiteStorage(path), SQLiteStorage(path)]
    raw = raw_transaction()
    stores[0].stage(raw, 'ethereum')
    analyzed = TransactionAnalyzer(registry=stores[0].wallet_registry).analyze_transaction(dict(raw, chain='ethereum'))
    signals = SignalGenerator().generate_signals(analyzed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.complete(raw['hash'], 'ethereum', signals, 'injected'), stores))
    assert sorted(results) == [False, True]
    assert count_signals(path) == 1
    assert len(stores[0].pending_notifications('injected')) == 1
