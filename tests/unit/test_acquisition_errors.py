from unittest.mock import Mock
import pytest
import requests
from src.fetching.explorer_api import ExplorerAPIClient, ExplorerError, ExplorerTimeout, InvalidExplorerResponse, IncompleteExplorerScan
from src.config.wallet_registry import Chain
from tests.integration.test_runtime_pipeline import raw_transaction, settings


def client(response=None):
    api = ExplorerAPIClient('offline-key', timeout=2)
    api.session = Mock()
    api.session.get.return_value.json.return_value = response
    return api


def test_no_transactions_is_success_and_timeout_is_explicit():
    api = client({'status': '0', 'message': 'No transactions found', 'result': []})
    assert api.get_wallet_transactions('0xabc', Chain.ETHEREUM) == []
    assert api.session.get.call_args.kwargs['timeout'] == 2
    api.session.get.side_effect = requests.Timeout('secret URL')
    with pytest.raises(ExplorerTimeout) as error:
        api.get_wallet_transactions('0xabc', Chain.ETHEREUM)
    assert 'secret URL' not in str(error.value)


@pytest.mark.parametrize('response,error', [
    ({'status': '0', 'message': 'NOTOK', 'result': 'Invalid key'}, ExplorerError),
    ([], InvalidExplorerResponse),
    ({'status': '1', 'result': 'not a list'}, InvalidExplorerResponse),
    ({'status': '1', 'result': [{}]}, InvalidExplorerResponse),
    ({'status': '0', 'message': 'No transactions found'}, InvalidExplorerResponse),
    ({'error': {'message': 'secret'}}, ExplorerError),
])
def test_invalid_response_not_reported_as_empty(response, error):
    with pytest.raises(error):
        client(response).get_wallet_transactions('0xabc', Chain.ETHEREUM)


def test_http_and_json_errors_are_distinct():
    api = client()
    api.session.get.side_effect = requests.ConnectionError('secret')
    with pytest.raises(ExplorerError, match='HTTP/connection'):
        api.get_wallet_transactions('0xabc', Chain.ETHEREUM)
    api = client()
    api.session.get.return_value.json.side_effect = ValueError()
    with pytest.raises(InvalidExplorerResponse, match='JSON'):
        api.get_wallet_transactions('0xabc', Chain.ETHEREUM)


def test_valid_transaction_list():
    api = client({'status': '1', 'result': [raw_transaction()]})
    assert len(api.get_wallet_transactions('0xabc', Chain.ETHEREUM)) == 1


@pytest.mark.parametrize('failure', [ExplorerTimeout('timeout'), InvalidExplorerResponse('invalid'), ExplorerError('API error')])
async def test_incomplete_scan_retains_chain_cursor(tmp_path, failure):
    from main import WhaleTracker
    blockchain = Mock()
    blockchain.get_latest_block_number.return_value = 120
    explorer = Mock()
    explorer.get_wallet_transactions.side_effect = failure
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=blockchain, explorer_client=explorer,
                           recipient=Mock())
    tracker.last_processed_blocks[Chain.ETHEREUM] = 100
    with pytest.raises(ExplorerError):
        await tracker.process_new_transactions()
    assert tracker.last_processed_blocks[Chain.ETHEREUM] == 100


async def test_full_explorer_page_does_not_advance_cursor(tmp_path):
    from main import WhaleTracker
    blockchain = Mock()
    blockchain.get_latest_block_number.return_value = 120
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = [dict(raw_transaction(), blockNumber='101')] * 100
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=blockchain, explorer_client=explorer, recipient=Mock())
    tracker.last_processed_blocks[Chain.ETHEREUM] = 100
    with pytest.raises(IncompleteExplorerScan):
        await tracker.process_new_transactions()
    assert tracker.last_processed_blocks[Chain.ETHEREUM] == 100


def test_requests_json_decode_error_is_classified_as_invalid_response():
    api = client()
    api.session.get.return_value.json.side_effect = requests.exceptions.JSONDecodeError('invalid', '', 0)
    with pytest.raises(InvalidExplorerResponse, match='JSON'):
        api.get_wallet_transactions('0xabc', Chain.ETHEREUM)
