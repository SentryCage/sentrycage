"""Acquisition and analysis share authoritative, persistent per-chain wallet metadata."""
from dataclasses import replace
import sqlite3
from unittest.mock import Mock
import pytest
from src.config.wallet_registry import ALL_WALLETS, Chain, WalletCategory, WalletInfo
from src.database.sqlite import add_wallet, get_connection
from src.orchestration.pipeline import SQLiteStorage, TransactionPipeline
from tests.integration.test_recovery import recipient
from tests.integration.test_runtime_pipeline import raw_transaction, settings

CUSTOM = '0x' + 'cd' * 20


async def test_sqlite_wallet_is_acquired_and_analyzed_with_its_threshold(tmp_path):
    from main import WhaleTracker
    config = replace(settings(tmp_path), transaction_threshold=1000)
    store = SQLiteStorage(config.db_path)
    add_wallet(CUSTOM, 'ethereum', label='Operator wallet', alert_threshold_eth=150,
               db_path=config.db_path, category='whale', tags=['custom'])
    rpc = Mock()
    rpc.get_latest_block_number.return_value = 120
    rpc.get_chain_id.return_value = 1
    explorer = Mock()
    explorer.get_wallet_transactions.side_effect = lambda **kwargs: [dict(raw_transaction(), to=CUSTOM,
        value=str(200 * 10**18), blockNumber='105')] if kwargs['address'] == CUSTOM else []
    tracker = WhaleTracker(config, storage=store, blockchain_client=rpc, explorer_client=explorer, recipient=recipient())
    await tracker.initialize()
    assert await tracker.process_new_transactions() == 1
    assert any(call.kwargs['address'] == CUSTOM for call in explorer.get_wallet_transactions.call_args_list)
    row = store.read()[0]
    assert row['wallet_address'] == CUSTOM
    assert row['metadata']['wallet_name'] == 'Operator wallet'
    assert row['metadata']['tags'] == ['custom']
    assert tracker.transaction_analyzer.registry is tracker.wallet_registry


async def test_same_address_uses_distinct_chain_metadata_and_thresholds(tmp_path):
    store = SQLiteStorage(str(tmp_path/'registry.db'))
    store.wallet_registry.put(WalletInfo(CUSTOM, WalletCategory.MARKET_MAKER, name='ETH wallet',
                                         chains=[Chain.ETHEREUM], threshold_override=700))
    store.wallet_registry.put(WalletInfo(CUSTOM, WalletCategory.WHALE, name='ARB wallet',
                                         chains=[Chain.ARBITRUM], threshold_override=150))
    pipeline = TransactionPipeline(store, recipient())
    raw = dict(raw_transaction(), to=CUSTOM, value=str(200 * 10**18))
    eth = pipeline.analyzer.analyze_transaction(dict(raw, chain='ethereum'))
    arb = pipeline.analyzer.analyze_transaction(dict(raw, chain='arbitrum'))
    assert not eth['is_significant'] and arb['is_significant']
    assert eth['to_wallet_info']['category'] == 'market_maker'
    assert arb['to_wallet_info']['name'] == 'ARB wallet'
    assert (await pipeline.process([raw], 'ethereum')).signals == []
    assert (await pipeline.process([raw], 'arbitrum')).signals[0].wallet_name == 'ARB wallet'


def test_seed_once_preserves_changes_removals_and_empty_registry(tmp_path):
    path = str(tmp_path/'seed.db')
    store = SQLiteStorage(path)
    original = ALL_WALLETS[1]
    store.wallet_registry.put(WalletInfo(original.address, WalletCategory.OTHER, name='Operator label',
                                        chains=[Chain.ETHEREUM], threshold_override=2000))
    store.wallet_registry.remove(original.address, Chain.ARBITRUM)
    resumed = SQLiteStorage(path)
    assert resumed.wallet_registry.lookup(original.address, 'ethereum').name == 'Operator label'
    assert resumed.wallet_registry.lookup(original.address, 'ethereum').threshold_override == 2000
    assert resumed.wallet_registry.lookup(original.address, 'arbitrum') is None
    with get_connection(path) as con:
        con.execute('DELETE FROM tracked_wallets')
    assert SQLiteStorage(path).wallet_registry.for_chain('ethereum') == []


def test_seeded_and_explicit_default_threshold_follow_product_setting(tmp_path):
    store = SQLiteStorage(str(tmp_path/'threshold.db'))
    pipeline = TransactionPipeline(store, recipient(), threshold=1000)
    assert not pipeline.analyzer.analyze_transaction(dict(raw_transaction(), chain='ethereum'))['is_significant']
    store.wallet_registry.put(WalletInfo(CUSTOM, WalletCategory.WHALE, chains=[Chain.ETHEREUM]))
    analyzed = pipeline.analyzer.analyze_transaction(dict(raw_transaction(), chain='ethereum', to=CUSTOM))
    assert analyzed['threshold_used'] == 1000 and not analyzed['is_significant']


async def test_new_wallet_is_seen_by_running_tracker_even_when_block_is_unchanged(tmp_path):
    from main import WhaleTracker
    rpc = Mock()
    rpc.get_latest_block_number.return_value = 120
    rpc.get_chain_id.return_value = 1
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = []
    tracker = WhaleTracker(settings(tmp_path), blockchain_client=rpc, explorer_client=explorer, recipient=recipient())
    await tracker.initialize()
    await tracker.process_new_transactions()
    old_scope = tracker.scan_scopes[Chain.ETHEREUM]
    tracker.wallet_registry.put(WalletInfo(CUSTOM, WalletCategory.WHALE, chains=[Chain.ETHEREUM], threshold_override=150))
    explorer.get_wallet_transactions.side_effect = lambda **kwargs: [dict(raw_transaction(), to=CUSTOM,
        blockNumber='100')] if kwargs['address'] == CUSTOM else []
    assert await tracker.process_new_transactions() == 1
    assert tracker.scan_scopes[Chain.ETHEREUM] != old_scope
    assert tracker.storage.load_checkpoint('ethereum', old_scope) == 120
    assert tracker.storage.load_checkpoint('ethereum') == 120


def test_legacy_wallet_migration_preserves_operator_values(tmp_path):
    path = str(tmp_path/'legacy.db')
    with sqlite3.connect(path) as con:
        con.execute('''CREATE TABLE tracked_wallets(address TEXT, chain TEXT, label TEXT,
                       alert_threshold_eth REAL NOT NULL DEFAULT 50, active INTEGER DEFAULT 1,
                       added_at TEXT DEFAULT 'old', PRIMARY KEY(address, chain))''')
        con.execute('INSERT INTO tracked_wallets(address,chain,label,alert_threshold_eth) VALUES (?,?,?,?)',
                    (ALL_WALLETS[1].address, 'ethereum', 'Existing label', 2000))
    store = SQLiteStorage(path)
    wallet = store.wallet_registry.lookup(ALL_WALLETS[1].address, 'ethereum')
    assert wallet.name == 'Existing label' and wallet.threshold_override == 2000
    assert wallet.category == WalletCategory.WHALE


def test_inactive_wallet_is_not_resurrected_or_analyzed_as_tracked(tmp_path):
    path = str(tmp_path/'inactive.db')
    store = SQLiteStorage(path)
    with get_connection(path) as con:
        con.execute('UPDATE tracked_wallets SET active=0 WHERE address=?', (ALL_WALLETS[1].address,))
    resumed = SQLiteStorage(path)
    assert resumed.wallet_registry.lookup(ALL_WALLETS[1].address, 'ethereum') is None
    assert TransactionPipeline(resumed, recipient()).analyzer.analyze_transaction(
        dict(raw_transaction(), chain='ethereum'))['to_wallet_info'] is None


@pytest.mark.parametrize('address,threshold', [('invalid', 100), (CUSTOM, 0), (CUSTOM, float('nan')),
                                             (CUSTOM, True), (CUSTOM, 'invalid')])
def test_invalid_updates_leave_registry_unchanged(tmp_path, address, threshold):
    store = SQLiteStorage(str(tmp_path/'invalid.db'))
    scope = store.wallet_registry.scope('ethereum')
    with pytest.raises(ValueError):
        store.wallet_registry.put(WalletInfo(address, WalletCategory.WHALE, threshold_override=threshold))
    assert store.wallet_registry.scope('ethereum') == scope


def test_corrupt_registry_does_not_silently_fall_back_to_static_examples(tmp_path):
    path = str(tmp_path/'corrupt.db')
    SQLiteStorage(path)
    with get_connection(path) as con:
        con.execute("UPDATE tracked_wallets SET category='invalid'")
    with pytest.raises(ValueError):
        SQLiteStorage(path)


@pytest.mark.parametrize('restart', [False, True])
async def test_registry_change_after_long_pause_preserves_unfinished_block_range(tmp_path, restart):
    from main import WhaleTracker
    config = settings(tmp_path)
    rpc = Mock()
    rpc.get_latest_block_number.return_value = 1200
    rpc.get_chain_id.return_value = 1
    explorer = Mock()
    explorer.get_wallet_transactions.return_value = []
    tracker = WhaleTracker(config, blockchain_client=rpc, explorer_client=explorer, recipient=recipient())
    await tracker.initialize()
    await tracker.process_new_transactions()
    tracker.wallet_registry.put(WalletInfo(CUSTOM, WalletCategory.WHALE, chains=[Chain.ETHEREUM]))
    rpc.get_latest_block_number.return_value = 5000
    explorer.get_wallet_transactions.side_effect = lambda **kwargs: [dict(raw_transaction(), to=CUSTOM,
        blockNumber='1300')] if kwargs['address'] == CUSTOM else []
    if restart:
        tracker = WhaleTracker(config, blockchain_client=rpc, explorer_client=explorer, recipient=recipient())
        await tracker.initialize()
    assert await tracker.process_new_transactions() == 1
    custom_calls = [call for call in explorer.get_wallet_transactions.call_args_list if call.kwargs['address'] == CUSTOM]
    assert custom_calls and all(call.kwargs['start_block'] == 1201 for call in custom_calls)
    assert tracker.storage.load_checkpoint('ethereum') == 5000


def test_invalid_inheritance_flag_does_not_hide_a_bad_threshold(tmp_path):
    path = str(tmp_path/'corrupt-threshold.db')
    SQLiteStorage(path)
    with get_connection(path) as con:
        con.execute('UPDATE tracked_wallets SET use_default_threshold=2, alert_threshold_eth=-1')
    with pytest.raises(ValueError, match='inheritance flag'):
        SQLiteStorage(path)
