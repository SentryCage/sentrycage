-- ============================================================
-- SentryCage — Self-Hosted SQLite Schema
-- Single-user local storage. No multi-tenancy.
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ============================================================
-- SIGNALS — storico segnali generati
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id              TEXT PRIMARY KEY,
    signal_type     TEXT NOT NULL,
    source          TEXT NOT NULL CHECK(source IN ('whale_tracker', 'trading_bot', 'ai_reasoning')),
    strength        TEXT NOT NULL CHECK(strength IN ('low', 'medium', 'high', 'very_high')),
    confidence      REAL,
    chain           TEXT,
    wallet_address  TEXT,
    transaction_hash TEXT,
    value_eth       REAL,
    reasoning_chain TEXT,              -- JSON array
    recommended_action TEXT CHECK(recommended_action IN ('LONG', 'SHORT', 'NEUTRAL', NULL)),
    metadata        TEXT,              -- JSON object
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at      TEXT               -- NULL = no expiry
);

CREATE INDEX IF NOT EXISTS idx_signals_created   ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_type      ON signals(signal_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_wallet    ON signals(wallet_address, chain);
CREATE INDEX IF NOT EXISTS idx_signals_strength  ON signals(strength, created_at DESC);

-- ============================================================
-- TRACKED WALLETS — wallet monitorati (persistente tra restart)
-- ============================================================
CREATE TABLE IF NOT EXISTS tracked_wallets (
    address             TEXT NOT NULL,
    chain               TEXT NOT NULL DEFAULT 'ethereum',
    label               TEXT,
    alert_threshold_eth REAL NOT NULL DEFAULT 50.0,
    active              INTEGER NOT NULL DEFAULT 1,
    category            TEXT NOT NULL DEFAULT 'whale',
    tags                TEXT NOT NULL DEFAULT '[]',
    description         TEXT,
    use_default_threshold INTEGER NOT NULL DEFAULT 0,
    added_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (address, chain)
);

CREATE INDEX IF NOT EXISTS idx_wallets_active ON tracked_wallets(active, chain);

-- ============================================================
-- PROCESSED TRANSACTIONS — deduplication (rimpiazza processed_txs set in-memory)
-- Risolve il memory leak in WhaleTracker: il set cresceva senza bound.
-- Retention: 30 giorni (pulizia via retention job o manuale).
-- ============================================================
CREATE TABLE IF NOT EXISTS processed_transactions (
    tx_hash         TEXT NOT NULL,
    chain           TEXT NOT NULL,
    wallet_address  TEXT,
    processed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (tx_hash, chain)
);

CREATE INDEX IF NOT EXISTS idx_processed_tx_time ON processed_transactions(processed_at);

-- Recovery journal: completion means local results and delivery intent are committed.
CREATE TABLE IF NOT EXISTS transaction_work (
    tx_hash TEXT NOT NULL,
    chain TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    wallet_address TEXT,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'completed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (tx_hash, chain)
);

CREATE TABLE IF NOT EXISTS scan_checkpoints (
    chain TEXT NOT NULL,
    scope TEXT NOT NULL,
    block_number INTEGER NOT NULL CHECK(block_number >= 0),
    PRIMARY KEY (chain, scope)
);

CREATE TABLE IF NOT EXISTS scan_checkpoint_heads (
    chain TEXT PRIMARY KEY,
    scope TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id TEXT PRIMARY KEY REFERENCES signals(id),
    route TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK(state IN ('pending', 'sending', 'sent', 'disabled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt REAL NOT NULL DEFAULT 0,
    lease_until REAL,
    owner TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON notification_outbox(state, next_attempt);

-- ============================================================
-- USER CONFIG — configurazione locale utente
-- ============================================================
CREATE TABLE IF NOT EXISTS user_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Valori default
INSERT OR IGNORE INTO user_config (key, value) VALUES
    ('polling_interval_seconds', '60'),
    ('min_signal_strength',      'medium'),
    ('max_signals_history_days', '90'),
    ('dry_run',                  'true');

-- ============================================================
-- LICENSE — license key per feature Pro/Enterprise
-- ============================================================
CREATE TABLE IF NOT EXISTS license (
    key_hash        TEXT PRIMARY KEY,  -- SHA-256 della license key raw
    tier            TEXT NOT NULL DEFAULT 'free' CHECK(tier IN ('free', 'pro', 'enterprise')),
    activated_at    TEXT,
    expires_at      TEXT,              -- NULL = perpetual
    features        TEXT               -- JSON array di feature abilitate
);
