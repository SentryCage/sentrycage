"""Authoritative per-chain SQLite wallet snapshot, shared by acquisition and analysis."""
import hashlib
import json
import math
import re
from src.config.wallet_registry import ALL_WALLETS, Chain, WalletCategory, WalletInfo
from src.database import sqlite


class WalletRegistry:
    def __init__(self, db_path):
        self.db_path = db_path
        self._seed_once()
        self.reload()

    def _seed_once(self):
        with sqlite.get_connection(self.db_path) as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT 1 FROM user_config WHERE key='wallet_registry_seeded'").fetchone():
                return
            for wallet in ALL_WALLETS:
                for chain in wallet.chains:
                    con.execute('''INSERT INTO tracked_wallets
                        (address, chain, label, alert_threshold_eth, category, tags, description, use_default_threshold)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(address, chain) DO NOTHING''',
                                (wallet.address, chain.value, wallet.name,
                                 50 if wallet.threshold_override is None else wallet.threshold_override,
                                 wallet.category.value, json.dumps(wallet.tags), wallet.description,
                                 int(wallet.threshold_override is None)))
            con.execute("INSERT INTO user_config(key, value) VALUES ('wallet_registry_seeded', '1')")

    @staticmethod
    def _validate(wallet, chain):
        if not re.fullmatch(r'0x[0-9a-f]{40}', wallet.address):
            raise ValueError('Wallet address must be a 20-byte hexadecimal address')
        if not isinstance(chain, Chain) or not isinstance(wallet.category, WalletCategory):
            raise ValueError('Unsupported wallet chain or category')
        threshold = wallet.threshold_override
        if threshold is not None and (not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
                                      or not math.isfinite(threshold) or threshold <= 0):
            raise ValueError('Wallet threshold must be positive and finite')
        if not isinstance(wallet.tags, list) or not all(isinstance(tag, str) for tag in wallet.tags):
            raise ValueError('Wallet tags must be a list of strings')
        if wallet.name is not None and not isinstance(wallet.name, str):
            raise ValueError('Wallet label must be a string')

    def reload(self):
        snapshot = {}
        for row in sqlite.get_wallets(db_path=self.db_path):
            if row['use_default_threshold'] not in (0, 1):
                raise ValueError('Invalid wallet threshold inheritance flag')
            chain = Chain(row['chain'])
            wallet = WalletInfo(row['address'], WalletCategory(row['category']), name=row['label'],
                                description=row['description'], chains=[chain], tags=row['tags'],
                                threshold_override=None if row['use_default_threshold'] else row['alert_threshold_eth'])
            self._validate(wallet, chain)
            snapshot[(chain.value, wallet.address)] = wallet
        self._wallets = snapshot

    def lookup(self, address, chain):
        return self._wallets.get((chain.value if isinstance(chain, Chain) else chain, address.lower()))

    def for_chain(self, chain):
        name = chain.value if isinstance(chain, Chain) else chain
        return [wallet for (network, _), wallet in sorted(self._wallets.items()) if network == name]

    def scope(self, chain):
        entries = [(wallet.address, wallet.category.value, wallet.name, wallet.threshold_override, wallet.tags)
                   for wallet in self.for_chain(chain)]
        return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()

    def put(self, wallet):
        """Create or update every explicitly selected chain; callers choose any removals separately."""
        for chain in wallet.chains:
            self._validate(wallet, chain)
        with sqlite.get_connection(self.db_path) as con:
            for chain in wallet.chains:
                con.execute('''INSERT INTO tracked_wallets
                    (address, chain, label, alert_threshold_eth, category, tags, description, use_default_threshold, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT(address, chain) DO UPDATE SET
                    label=excluded.label, alert_threshold_eth=excluded.alert_threshold_eth,
                    category=excluded.category, tags=excluded.tags, description=excluded.description,
                    use_default_threshold=excluded.use_default_threshold, active=1''',
                            (wallet.address, chain.value, wallet.name,
                             50 if wallet.threshold_override is None else wallet.threshold_override,
                             wallet.category.value, json.dumps(wallet.tags), wallet.description,
                             int(wallet.threshold_override is None)))
        self.reload()

    def remove(self, address, chain):
        sqlite.remove_wallet(address, chain.value if isinstance(chain, Chain) else chain, self.db_path)
        self.reload()
