"""Fixed-range pagination through the real client and restartable tracker."""
from dataclasses import replace
from unittest.mock import Mock
import pytest
from src.config.wallet_registry import Chain
from src.database.sqlite import count_signals
from src.fetching.explorer_api import (ExplorerAPIClient, ExplorerTimeout, IncompleteExplorerScan,
                                      InvalidExplorerResponse, wallet_transaction_pages)
from tests.integration.test_recovery import recipient
from tests.integration.test_runtime_pipeline import raw_transaction, settings


def tx(index, block=15):
    return dict(raw_transaction(), hash='0x' + f'{index:064x}', blockNumber=str(block))


def api_with_pages(pages):
    api = ExplorerAPIClient('offline-key')
    api.session = Mock()
    responses = []
    for rows in pages:
        response = Mock()
        response.json.return_value = {'status': '1', 'result': rows}
        responses.append(response)
    api.session.get.side_effect = responses
    return api


def scan(api, **kwargs):
    return list(wallet_transaction_pages(api, '0xabc', Chain.ETHEREUM, 10, 20, page_size=2, **kwargs))


def test_real_client_reads_all_pages_deduplicates_and_keeps_fixed_bounds():
    api = api_with_pages([[tx(1), tx(2)], [tx(2), tx(3)], []])
    pages = scan(api)
    assert [row['hash'] for page in pages for row in page] == [tx(i)['hash'] for i in (1, 2, 3)]
    requests = [call.kwargs['params'] for call in api.session.get.call_args_list]
    assert [request['page'] for request in requests] == [1, 2, 3]
    assert all((request['startblock'], request['endblock'], request['sort'], request['offset']) ==
               (10, 20, 'asc', 2) for request in requests)


def test_exact_full_last_page_requires_empty_page_to_finish():
    api = api_with_pages([[tx(1), tx(2)], []])
    assert len(scan(api)) == 1
    assert api.session.get.call_count == 2


def test_short_page_finishes_without_extra_request():
    api = api_with_pages([[tx(1)]])
    assert scan(api) == [[tx(1)]]
    assert api.session.get.call_count == 1


def test_repeated_full_page_is_incomplete_not_success():
    with pytest.raises(IncompleteExplorerScan, match='no progress'):
        scan(api_with_pages([[tx(1), tx(2)], [tx(1), tx(2)]]))


def test_budget_exhaustion_is_incomplete_not_success():
    with pytest.raises(IncompleteExplorerScan, match='budget'):
        scan(api_with_pages([[tx(1), tx(2)]]), max_pages=1)


@pytest.mark.parametrize('row', [tx(1, 9), tx(1, 21), dict(tx(1), blockNumber='bad'),
                                dict(tx(1), value='bad')])
def test_invalid_or_out_of_range_rows_fail(row):
    with pytest.raises(InvalidExplorerResponse):
        scan(api_with_pages([[row]]))


def test_backwards_order_fails():
    with pytest.raises(InvalidExplorerResponse, match='ordering'):
        scan(api_with_pages([[tx(1, 16), tx(2, 15)]]))


def test_conflicting_duplicate_fails():
    with pytest.raises(InvalidExplorerResponse, match='conflicting'):
        scan(api_with_pages([[tx(1), tx(2)], [dict(tx(2), value='1')]]))


async def test_failure_mid_scan_preserves_checkpoint_and_retry_does_not_duplicate(tmp_path):
    from main import WhaleTracker
    rpc = Mock()
    rpc.get_latest_block_number.return_value = 20
    rpc.get_chain_id.return_value = 1
    explorer = Mock()
    explorer.get_wallet_transactions.side_effect = [[tx(1), tx(2)], ExplorerTimeout('offline')]
    target = recipient()
    config = replace(settings(tmp_path), explorer_page_size=2)
    tracker = WhaleTracker(config, blockchain_client=rpc, explorer_client=explorer, recipient=target)
    tracker.storage.save_checkpoint('ethereum', 10)
    await tracker.initialize()
    with pytest.raises(ExplorerTimeout):
        await tracker.process_new_transactions()
    assert tracker.storage.load_checkpoint('ethereum') == 10
    assert count_signals(config.db_path) == 2
    assert tracker.storage.outstanding_notifications() == 2
    explorer.get_wallet_transactions.side_effect = lambda **kwargs: [tx(1), tx(2)] if kwargs['page'] == 1 else [tx(3)]
    restarted = WhaleTracker(config, blockchain_client=rpc, explorer_client=explorer, recipient=target)
    await restarted.initialize()
    assert await restarted.process_new_transactions() == 1
    assert restarted.storage.load_checkpoint('ethereum') == 20
    assert count_signals(config.db_path) == 3
    assert target.send_signal.await_count == 3


async def test_rpc_block_regression_does_not_rewind_checkpoint(tmp_path):
    from main import WhaleTracker
    rpc = Mock()
    rpc.get_latest_block_number.return_value = 9
    rpc.get_chain_id.return_value = 1
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=rpc, explorer_client=Mock(), recipient=recipient())
    tracker.storage.save_checkpoint('ethereum', 10)
    await tracker.initialize()
    with pytest.raises(IncompleteExplorerScan, match='precedes checkpoint'):
        await tracker.process_new_transactions()
    assert tracker.storage.load_checkpoint('ethereum') == 10
