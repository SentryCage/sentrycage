# Changelog

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
for local-only storage. Docker wallet-registry edits now require rebuild/recreation.

Verified scope: offline Python 3.12.3 and mocked services. **Demo verified; live unverified.**
Docker runtime/AI profile/persistence and real provider/delivery checks remain pending.
Early transaction marking, in-memory cursor, pagination and Telegram outbox/duplicates remain follow-up
work; see [architecture](docs/architecture.md). No publication or repository setting changes were made.
