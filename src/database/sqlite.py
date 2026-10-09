"""
SQLite local storage — self-hosted single-user database.
Rimpiazza MongoDB (multi-tenant SaaS) con storage locale senza dipendenze esterne.
"""
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# Path del DB: da env oppure ./data/whale_tracker.db
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "whale_tracker.db"
)
DB_PATH = os.getenv("DB_PATH", _DEFAULT_DB_PATH)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# ============================================================
# Connection management
# ============================================================

def init_db(db_path: str = DB_PATH) -> None:
    """Crea il database e applica lo schema se non esiste."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.executescript(_SCHEMA_PATH.read_text())
        columns = {row[1] for row in con.execute('PRAGMA table_info(tracked_wallets)')}
        for name, declaration in (
            ('category', "TEXT NOT NULL DEFAULT 'whale'"), ('tags', "TEXT NOT NULL DEFAULT '[]'"),
            ('description', 'TEXT'), ('use_default_threshold', 'INTEGER NOT NULL DEFAULT 0')
        ):
            if name not in columns:
                con.execute(f'ALTER TABLE tracked_wallets ADD COLUMN {name} {declaration}')
    logger.info("Database SQLite inizializzato: %s", db_path)


@contextmanager
def get_connection(db_path: str = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager per connessione SQLite con row_factory."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ============================================================
# Signal operations
# ============================================================

def save_signal(signal: dict[str, Any], db_path: str = DB_PATH) -> str:
    """Salva un segnale. Restituisce l'id inserito."""
    with get_connection(db_path) as con:
        return insert_signal(con, signal)


def insert_signal(con: sqlite3.Connection, signal: dict[str, Any]) -> str:
    """Insert using the caller's transaction (also used by atomic recovery commits)."""
    import uuid
    signal_id = signal.get("id") or str(uuid.uuid4())
    con.execute(
            """
            INSERT INTO signals
                (id, signal_type, source, strength, confidence, chain,
                 wallet_address, transaction_hash, value_eth,
                 reasoning_chain, recommended_action, metadata, expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                signal_id,
                signal.get("signal_type", "unusual_activity"),
                signal.get("source", "whale_tracker"),
                signal.get("strength", "medium"),
                signal.get("confidence"),
                signal.get("chain"),
                signal.get("wallet_address"),
                signal.get("transaction_hash"),
                signal.get("value_eth"),
                json.dumps(signal.get("reasoning_chain", [])),
                signal.get("recommended_action"),
                json.dumps(signal.get("metadata", {})),
                signal.get("expires_at"),
            ),
    )
    return signal_id


def get_signals(
    signal_type: Optional[str] = None,
    chain: Optional[str] = None,
    min_confidence: Optional[float] = None,
    min_value_eth: Optional[float] = None,
    since: Optional[datetime] = None,
    wallet_address: Optional[str] = None,
    min_strength: Optional[str] = None,
    limit: int = 100,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    """Recupera segnali con filtri opzionali, ordinati per data decrescente."""
    _STRENGTH_RANK = {"low": 0, "medium": 1, "high": 2, "very_high": 3}

    where_clauses: list[str] = []
    params: list[Any] = []

    if signal_type:
        where_clauses.append("signal_type = ?")
        params.append(signal_type)
    if chain:
        where_clauses.append("chain = ?")
        params.append(chain)
    if min_confidence is not None:
        where_clauses.append("confidence >= ?")
        params.append(min_confidence)
    if min_value_eth is not None:
        where_clauses.append("value_eth >= ?")
        params.append(min_value_eth)
    if since:
        where_clauses.append("created_at >= ?")
        params.append(since.isoformat())
    if wallet_address:
        where_clauses.append("wallet_address = ?")
        params.append(wallet_address.lower())
    if min_strength and min_strength in _STRENGTH_RANK:
        rank = _STRENGTH_RANK[min_strength]
        eligible = [s for s, r in _STRENGTH_RANK.items() if r >= rank]
        where_clauses.append(f"strength IN ({','.join('?' * len(eligible))})")
        params.extend(eligible)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    with get_connection(db_path) as con:
        rows = con.execute(
            f"SELECT * FROM signals {where_sql} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()

    return [_row_to_dict(r) for r in rows]


def count_signals(db_path: str = DB_PATH) -> int:
    with get_connection(db_path) as con:
        return con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]


# ============================================================
# Tracked wallet operations
# ============================================================

def add_wallet(
    address: str,
    chain: str = "ethereum",
    label: Optional[str] = None,
    alert_threshold_eth: float = 50.0,
    db_path: str = DB_PATH,
    *, category: str = 'whale', tags: Optional[list[str]] = None, description: Optional[str] = None,
) -> None:
    """Aggiunge un wallet da tracciare. No-op se esiste già."""
    with get_connection(db_path) as con:
        con.execute(
            """
            INSERT OR IGNORE INTO tracked_wallets
                (address, chain, label, alert_threshold_eth, category, tags, description, use_default_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (address.lower(), chain, label, 50 if alert_threshold_eth is None else alert_threshold_eth,
             category, json.dumps(tags or []), description, int(alert_threshold_eth is None)),
        )


def remove_wallet(address: str, chain: str = "ethereum", db_path: str = DB_PATH) -> None:
    """Rimuove un wallet dalla lista monitorata."""
    with get_connection(db_path) as con:
        con.execute(
            "DELETE FROM tracked_wallets WHERE address = ? AND chain = ?",
            (address.lower(), chain),
        )


def get_wallets(
    chain: Optional[str] = None,
    active_only: bool = True,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    """Restituisce i wallet tracciati."""
    where = []
    params: list[Any] = []
    if active_only:
        where.append("active = 1")
    if chain:
        where.append("chain = ?")
        params.append(chain)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with get_connection(db_path) as con:
        rows = con.execute(
            f"SELECT * FROM tracked_wallets {where_sql} ORDER BY added_at",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_wallets(db_path: str = DB_PATH) -> int:
    with get_connection(db_path) as con:
        return con.execute(
            "SELECT COUNT(*) FROM tracked_wallets WHERE active = 1"
        ).fetchone()[0]


# ============================================================
# Processed transactions — deduplication
# ============================================================

def is_tx_processed(tx_hash: str, chain: str, db_path: str = DB_PATH) -> bool:
    """True se la transazione è già stata processata."""
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM processed_transactions WHERE tx_hash = ? AND chain = ?",
            (tx_hash, chain),
        ).fetchone()
    return row is not None


def mark_tx_processed(
    tx_hash: str,
    chain: str,
    wallet_address: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Marca una transazione come processata."""
    with get_connection(db_path) as con:
        con.execute(
            "INSERT OR IGNORE INTO processed_transactions (tx_hash, chain, wallet_address) VALUES (?,?,?)",
            (tx_hash, chain, wallet_address),
        )


def cleanup_old_transactions(days: int = 30, db_path: str = DB_PATH) -> int:
    """Elimina transazioni processate più vecchie di `days` giorni. Restituisce righe eliminate."""
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
    with get_connection(db_path) as con:
        cur = con.execute(
            """DELETE FROM processed_transactions WHERE processed_at < ?
               AND NOT EXISTS (SELECT 1 FROM transaction_work w
                               WHERE w.tx_hash = processed_transactions.tx_hash
                                 AND w.chain = processed_transactions.chain)""", (cutoff,)
        )
    deleted = cur.rowcount
    if deleted:
        logger.info("Cleanup: eliminate %d transazioni processate (>%d giorni)", deleted, days)
    return deleted


# ============================================================
# User config
# ============================================================

def get_config(key: str, default: Optional[str] = None, db_path: str = DB_PATH) -> Optional[str]:
    """Legge un valore di configurazione."""
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT value FROM user_config WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_config(key: str, value: str, db_path: str = DB_PATH) -> None:
    """Scrive un valore di configurazione."""
    with get_connection(db_path) as con:
        con.execute(
            """
            INSERT INTO user_config (key, value, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (key, value),
        )


# ============================================================
# License
# ============================================================

def get_license_tier(db_path: str = DB_PATH) -> str:
    """Restituisce il tier attivo ('free', 'pro', 'enterprise')."""
    with get_connection(db_path) as con:
        row = con.execute(
            """
            SELECT tier FROM license
            WHERE (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ORDER BY CASE tier
                WHEN 'enterprise' THEN 0
                WHEN 'pro'        THEN 1
                ELSE                   2
            END
            LIMIT 1
            """
        ).fetchone()
    return row["tier"] if row else "free"


# ============================================================
# Helpers
# ============================================================

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converte una Row SQLite in dict, deserializzando i campi JSON."""
    d = dict(row)
    for field in ("reasoning_chain", "metadata", "features", "tags"):
        if field in d and d[field] is not None:
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
