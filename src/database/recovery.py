"""Durable local processing and notification intent, using one SQLite transaction."""
import hashlib
import json
import uuid
from src.database import sqlite


class RecoveryStore:
    def __init__(self, db_path):
        self.db_path = db_path
        sqlite.init_db(db_path)

    def stage(self, raw, chain, wallet_address=None):
        tx_hash = raw.get('hash')
        if not isinstance(tx_hash, str) or not tx_hash:
            raise ValueError('Transaction is missing hash')
        with sqlite.get_connection(self.db_path) as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM processed_transactions WHERE tx_hash=? AND chain=?',
                           (tx_hash, chain)).fetchone():
                return None
            con.execute('''INSERT INTO transaction_work(tx_hash, chain, raw_json, wallet_address)
                           VALUES (?, ?, ?, ?) ON CONFLICT(tx_hash, chain) DO NOTHING''',
                        (tx_hash, chain, json.dumps(raw), wallet_address))
            row = con.execute('SELECT * FROM transaction_work WHERE tx_hash=? AND chain=?',
                              (tx_hash, chain)).fetchone()
            if row['state'] == 'completed':
                return None
            con.execute('UPDATE transaction_work SET attempts=attempts+1 WHERE tx_hash=? AND chain=?',
                        (tx_hash, chain))
            return json.loads(row['raw_json'])

    def pending_work(self, chains=None):
        with sqlite.get_connection(self.db_path) as con:
            rows = con.execute("SELECT * FROM transaction_work WHERE state='pending' ORDER BY rowid").fetchall()
        return [dict(row) for row in rows if chains is None or row['chain'] in chains]

    def record_error(self, tx_hash, chain, error):
        with sqlite.get_connection(self.db_path) as con:
            con.execute('UPDATE transaction_work SET last_error=? WHERE tx_hash=? AND chain=?',
                        (type(error).__name__, tx_hash, chain))

    def complete(self, tx_hash, chain, signals, route):
        """Commit all signals, immutable messages and completion atomically, before sending."""
        snapshots = []
        for index, signal in enumerate(signals):
            payload = signal.to_dict()
            if payload.get('transaction_hash') != tx_hash or payload.get('chain') != chain:
                raise ValueError('Signal does not reference the staged transaction')
            payload['id'] = hashlib.sha256(f'{chain}:{tx_hash}:{index}'.encode()).hexdigest()
            payload['source'] = 'ai_reasoning' if payload.get('provenance', {}).get('origin') == 'ai' else 'whale_tracker'
            payload['metadata'] = payload.copy()
            snapshots.append((payload, signal.get_message()))
        with sqlite.get_connection(self.db_path) as con:
            con.execute('BEGIN IMMEDIATE')
            work = con.execute('SELECT * FROM transaction_work WHERE tx_hash=? AND chain=?',
                               (tx_hash, chain)).fetchone()
            if work is None:
                raise ValueError('Transaction was not staged')
            if work['state'] == 'completed':
                return False
            for payload, message in snapshots:
                signal_id = sqlite.insert_signal(con, payload)
                con.execute('''INSERT INTO notification_outbox(id, route, message, payload, state)
                               VALUES (?, ?, ?, ?, ?)''',
                            (signal_id, route, message, json.dumps(payload),
                             'disabled' if route == 'disabled' else 'pending'))
            con.execute('''INSERT INTO processed_transactions(tx_hash, chain, wallet_address)
                           VALUES (?, ?, ?) ON CONFLICT(tx_hash, chain) DO NOTHING''',
                        (tx_hash, chain, work['wallet_address']))
            con.execute("UPDATE transaction_work SET state='completed', last_error=NULL WHERE tx_hash=? AND chain=?",
                        (tx_hash, chain))
        return True

    def _scope(self, chain, scope):
        if scope is not None:
            return scope
        registry = getattr(self, 'wallet_registry', None)
        return registry.scope(chain) if registry is not None else 'static-pilot'

    def load_checkpoint(self, chain, scope=None):
        scope = self._scope(chain, scope)
        with sqlite.get_connection(self.db_path) as con:
            row = con.execute('SELECT block_number FROM scan_checkpoints WHERE chain=? AND scope=?',
                              (chain, scope)).fetchone()
        return row['block_number'] if row else None

    def save_checkpoint(self, chain, block, scope=None):
        scope = self._scope(chain, scope)
        with sqlite.get_connection(self.db_path) as con:
            con.execute('''INSERT INTO scan_checkpoints(chain, scope, block_number) VALUES (?, ?, ?)
                           ON CONFLICT(chain, scope) DO UPDATE SET
                           block_number=MAX(block_number, excluded.block_number)''', (chain, scope, block))
            con.execute('''INSERT INTO scan_checkpoint_heads(chain, scope) VALUES (?, ?)
                           ON CONFLICT(chain) DO UPDATE SET scope=excluded.scope''', (chain, scope))

    def prepare_checkpoint(self, chain, scope, initial_block):
        """A registry change keeps unfinished historical ranges from the previous scope."""
        with sqlite.get_connection(self.db_path) as con:
            con.execute('BEGIN IMMEDIATE')
            saved = con.execute('SELECT block_number FROM scan_checkpoints WHERE chain=? AND scope=?',
                                (chain, scope)).fetchone()
            if saved is None:
                previous = con.execute('''SELECT c.block_number FROM scan_checkpoint_heads h
                    JOIN scan_checkpoints c ON c.chain=h.chain AND c.scope=h.scope WHERE h.chain=?''', (chain,)).fetchone()
                if previous is None:
                    # Conservative upgrade for checkpoints created before heads existed.
                    previous = con.execute('SELECT MIN(block_number) AS block_number FROM scan_checkpoints WHERE chain=?',
                                           (chain,)).fetchone()
                block = min(previous['block_number'], initial_block) if previous and previous['block_number'] is not None else initial_block
                con.execute('INSERT INTO scan_checkpoints(chain, scope, block_number) VALUES (?, ?, ?)', (chain, scope, block))
            else:
                block = saved['block_number']
            con.execute('''INSERT INTO scan_checkpoint_heads(chain, scope) VALUES (?, ?)
                           ON CONFLICT(chain) DO UPDATE SET scope=excluded.scope''', (chain, scope))
        return block

    def pending_notifications(self, route):
        with sqlite.get_connection(self.db_path) as con:
            return [dict(row) for row in con.execute(
                "SELECT * FROM notification_outbox WHERE route=? AND state='pending' ORDER BY rowid", (route,))]

    def claim_notification(self, route, now, lease_seconds, chains=None):
        """Exclusive, expiring claim; a stale worker cannot acknowledge a new owner's claim."""
        owner = uuid.uuid4().hex
        with sqlite.get_connection(self.db_path) as con:
            con.execute('BEGIN IMMEDIATE')
            chain_filter, params = self._chain_filter(chains)
            row = con.execute('''SELECT o.* FROM notification_outbox o JOIN signals s ON s.id=o.id
                WHERE route=? AND ((state='pending' AND next_attempt<=?)
                                   OR (state='sending' AND lease_until<=?))'''
                              + chain_filter + ' ORDER BY o.rowid LIMIT 1', [route, now, now] + params).fetchone()
            if row is None:
                return None
            con.execute("""UPDATE notification_outbox SET state='sending', attempts=attempts+1,
                           owner=?, lease_until=? WHERE id=?""", (owner, now + lease_seconds, row['id']))
            claimed = dict(row)
            claimed.update(owner=owner, attempts=row['attempts'] + 1)
            return claimed

    @staticmethod
    def _chain_filter(chains):
        if chains is None:
            return '', []
        if not chains:
            return ' AND 0', []
        return ' AND s.chain IN (' + ','.join('?' for _ in chains) + ')', list(chains)

    def outstanding_notifications(self, chains=None):
        chain_filter, params = self._chain_filter(chains)
        with sqlite.get_connection(self.db_path) as con:
            return con.execute('''SELECT COUNT(*) FROM notification_outbox o JOIN signals s ON s.id=o.id
                WHERE state IN ('pending', 'sending')''' + chain_filter, params).fetchone()[0]

    def record_delivery(self, notification_id, owner, state, error=None, next_attempt=0):
        with sqlite.get_connection(self.db_path) as con:
            updated = con.execute('''UPDATE notification_outbox SET state=?, last_error=?, next_attempt=?,
                                    owner=NULL, lease_until=NULL WHERE id=? AND owner=? AND state='sending' ''',
                                  (state, error, next_attempt, notification_id, owner))
            return updated.rowcount == 1
