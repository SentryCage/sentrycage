"""Notification delivery leases, restart, destination binding and ambiguous sends."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
import pytest
from src.database.sqlite import get_connection
from src.orchestration.pipeline import SQLiteStorage, TransactionPipeline, DeliveryError
from tests.integration.test_recovery import recipient
from tests.integration.test_runtime_pipeline import raw_transaction, settings


async def queue(tmp_path, target=None, chain='ethereum'):
    store = SQLiteStorage(str(tmp_path/'queue.db'))
    target = target or recipient()
    pipeline = TransactionPipeline(store, target)
    await pipeline.process([raw_transaction()], chain, deliver=False)
    return store


async def test_retry_wait_survives_restart_and_saved_message_is_reused(tmp_path):
    target = recipient()
    target.send_signal.side_effect = TimeoutError('secret endpoint')
    store = await queue(tmp_path, target)
    now = [100.0]
    pipeline = TransactionPipeline(store, target, clock=lambda: now[0])
    with pytest.raises(DeliveryError):
        await pipeline.flush_notifications()
    row = store.pending_notifications('injected')[0]
    assert (row['attempts'], row['next_attempt'], row['last_error']) == (1, 105, 'TimeoutError')
    with pytest.raises(DeliveryError):
        await pipeline.flush_notifications()
    assert target.send_signal.await_count == 1
    target.send_signal.side_effect = None
    now[0] = 105
    restarted = TransactionPipeline(SQLiteStorage(store.db_path), target, clock=lambda: now[0])
    assert await restarted.flush_notifications() == [True]
    assert target.send_signal.await_count == 2
    assert target.send_signal.await_args.args[0].get_message() == row['message']
    assert store.outstanding_notifications() == 0


async def test_claim_is_exclusive_expires_and_fences_old_owner(tmp_path):
    store = await queue(tmp_path)
    other = SQLiteStorage(store.db_path)
    first = store.claim_notification('injected', 100, 60)
    assert other.claim_notification('injected', 159, 60) is None
    second = other.claim_notification('injected', 160, 60)
    assert first['owner'] != second['owner'] and second['attempts'] == 2
    assert not store.record_delivery(first['id'], first['owner'], 'sent')
    assert other.record_delivery(second['id'], second['owner'], 'sent')
    assert other.outstanding_notifications() == 0


async def test_change_destination_never_redirects_pending_notifications(tmp_path):
    original = recipient()
    original.delivery_key = 'telegram:-1001'
    store = await queue(tmp_path, original)
    replacement = recipient()
    replacement.delivery_key = 'telegram:-1002'
    with pytest.raises(DeliveryError):
        await TransactionPipeline(store, replacement).flush_notifications()
    replacement.send_signal.assert_not_awaited()
    assert store.pending_notifications('telegram:-1001')


async def test_disabled_notifications_pause_old_queue_and_new_signals_stay_local(tmp_path):
    store = await queue(tmp_path)
    disabled = recipient()
    disabled.delivery_key = 'disabled'
    pipeline = TransactionPipeline(store, disabled)
    assert await pipeline.flush_notifications() == []
    await pipeline.process([dict(raw_transaction(), hash='0x' + 'cd' * 32)], 'ethereum')
    disabled.send_signal.assert_not_awaited()
    assert store.outstanding_notifications() == 1
    with get_connection(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM notification_outbox WHERE state='disabled'").fetchone()[0] == 1


async def test_cancelled_worker_leaves_lease_for_restart(tmp_path):
    target = recipient()
    entered = asyncio.Event()

    async def wait_forever(signal):
        entered.set()
        await asyncio.Event().wait()

    target.send_signal.side_effect = wait_forever
    store = await queue(tmp_path, target)
    task = asyncio.create_task(TransactionPipeline(store, target, clock=lambda: 100).flush_notifications())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    resumed_target = recipient()
    with pytest.raises(DeliveryError):
        await TransactionPipeline(store, resumed_target, clock=lambda: 159).flush_notifications()
    resumed_target.send_signal.assert_not_awaited()
    await TransactionPipeline(store, resumed_target, clock=lambda: 160).flush_notifications()
    resumed_target.send_signal.assert_awaited_once()


async def test_actual_worker_exit_after_send_can_cause_a_duplicate(tmp_path):
    store = await queue(tmp_path)
    sent = tmp_path/'sent.txt'
    root = Path(__file__).resolve().parents[2]
    program = '''import asyncio, os, sys
from pathlib import Path
from src.orchestration.pipeline import SQLiteStorage, TransactionPipeline
class LocalRecipient:
    delivery_key = 'injected'
    async def send_signal(self, signal):
        Path(sys.argv[2]).write_text(signal.get_message())
        os._exit(17)
asyncio.run(TransactionPipeline(SQLiteStorage(sys.argv[1]), LocalRecipient(), clock=lambda:100).flush_notifications())
'''
    result = subprocess.run([sys.executable, '-c', program, store.db_path, str(sent)], cwd=root,
                            env={'PATH': os.environ['PATH'], 'PYTHONUTF8': '1'}, timeout=20)
    assert result.returncode == 17 and sent.exists()
    target = recipient()
    await TransactionPipeline(SQLiteStorage(store.db_path), target, clock=lambda: 160).flush_notifications()
    assert target.send_signal.await_args.args[0].get_message() == sent.read_text()
    # First send happened, but its receipt was lost: this proves the documented ambiguity.
    target.send_signal.assert_awaited_once()


async def test_queue_for_disabled_chain_is_retained_without_delivery(tmp_path):
    store = await queue(tmp_path, chain='arbitrum')
    target = recipient()
    await TransactionPipeline(store, target, enabled_chains=['ethereum']).flush_notifications()
    target.send_signal.assert_not_awaited()
    assert store.outstanding_notifications() == 1


async def test_real_pipeline_timeout_is_bounded_and_scheduled(tmp_path):
    target = recipient()

    async def wait_forever(signal):
        await asyncio.Event().wait()

    target.send_signal.side_effect = wait_forever
    store = await queue(tmp_path, target)
    with pytest.raises(DeliveryError):
        await TransactionPipeline(store, target, clock=lambda: 100, delivery_timeout=.01).flush_notifications()
    assert store.pending_notifications('injected')[0]['last_error'] == 'TimeoutError'


async def test_delivery_failure_does_not_block_local_scan_checkpoint(tmp_path):
    from main import WhaleTracker
    rpc = Mock()
    rpc.get_chain_id.return_value = 1
    rpc.get_latest_block_number.return_value = 1000
    explorer = Mock()
    explorer.get_wallet_transactions.side_effect = lambda **kwargs: [dict(raw_transaction(), blockNumber=str(kwargs['start_block']))]
    target = recipient()
    target.send_signal.return_value = False
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=rpc, explorer_client=explorer, recipient=target)
    await tracker.initialize()
    with pytest.raises(DeliveryError):
        await tracker.process_new_transactions()
    assert tracker.storage.load_checkpoint('ethereum') == 1000
    assert tracker.storage.outstanding_notifications() == 1
    assert len(tracker.storage.read()) == 1
