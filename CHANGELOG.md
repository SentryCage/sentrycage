# Changelog

## Offline reliability extension — 2026-10-09 (local, unpublished)

- Add a persistent transaction journal and registry-scoped checkpoints, with restart recovery.
- Commit signals, immutable notification intent and local completion atomically before sending.
- Add leased notification delivery, durable attempts, exponential retry waiting and recipient binding.
  Ambiguous sends can duplicate; once returns 3 while enabled-chain notifications remain pending.
- Read ascending explorer pages over fixed block bounds; deduplicate and fail incomplete/inconsistent scans.
- Share an authoritative SQLite wallet registry with seed-once examples and per-chain metadata/thresholds.
  Registry changes during downtime preserve the unfinished range; runtime edits reload each cycle.
- Preserve AI/rule/fallback provenance, selected provider/model and every serialized signal field.
  Require full hash/chain references, reject invalid AI output and label rule fallback explicitly.

Migration: back up the database; new tables and wallet columns are additive. Existing wallet values,
signals and legacy markers remain. Configure runtime wallets through the README SQLite example;
static-file edits only affect fresh seeds. Old delivery/raw data cannot be reconstructed retrospectively.
Process-exit/rollback/retry/pagination/wallet/provenance tests are offline with simulated services.
Live services and Docker runtime remain unverified; reorganizations, automatic retention and historical
AI/data correctness audits remain open. See [architecture](docs/architecture.md).

## Pilot candidate — 2026-10-09 (local, unpublished)

- Validate configuration before clients/storage; default to Ethereum and AI off.
- Accept explicitly enabled Ethereum/Arbitrum endpoints; reject placeholders and conflicting aliases.
- Share raw analysis, signal generation, SQLite persistence and delivery between live and offline demo.
- Deliver normal monitoring alerts to the configured Telegram chat; distinguish disabled, missing,
  successful and failed delivery. Verify token connectivity before claiming connection.
- Add credential-free `--demo`, read-only `--doctor` and single-scan `--once` with scriptable exit codes.
- Surface acquisition/delivery/storage errors and retain cursors on incomplete scans.
- Bound service requests, reject malformed explorer data, and fail conservatively on full explorer pages.
- Redact configured credentials and Telegram bot URLs from logs; avoid import-time dotenv/log/database I/O.
- Make Docker base service independent of optional AI downloads; bound Ollama initialization and preserve
  a named data volume. Remove the old database-writing healthcheck.
- Update README, project instructions, regression CI and actual synthetic demo captures/terminal recording.

Migration: use [configuration and migration](docs/configuration.md). `INFURA_API_KEY` alone is insufficient;
set complete RPC URLs. AI is now off, default provider Ollama; migrate `ENABLE_AI_REASONING` and
`OLLAMA_BASE_URL` to `ENABLE_REASONING` and `OLLAMA_URL`. Set `NOTIFICATIONS_ENABLED=false` explicitly
for local-only storage. This baseline used the static runtime wallet registry.

Verified scope: offline Python 3.12.3 and mocked services. **Demo verified; live unverified.**
Docker runtime/AI profile/persistence and real provider/delivery checks remain pending.
At this baseline, early marking, in-memory cursor, pagination and Telegram outbox were follow-up work;
the extension above implements them with explicit duplicate semantics. No publication was made.
