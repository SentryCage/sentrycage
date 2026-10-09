# Configuration and migration

Live startup validates settings before clients or SQLite are constructed. Demo ignores configuration.
Only `ethereum` and `arbitrum` are accepted in `ENABLED_CHAINS`; the default is Ethereum.
Supply `ETHERSCAN_API_KEY` and the corresponding `ETHEREUM_RPC_URL` / `ARBITRUM_RPC_URL` for enabled chains only.
`INFURA_API_KEY` does not construct an RPC URL: provide the complete URL yourself.
Empty values and placeholders fail startup, and errors report variable names without values.

Notifications default to enabled: configure `TELEGRAM_BOT_TOKEN` and a nonzero integer `TELEGRAM_CHAT_ID`.
Negative group IDs work. Choose `NOTIFICATIONS_ENABLED=false` explicitly for local-only signals.
`TEST_CHAT_ID` is optional; test/preview otherwise use the configured destination and send real messages.
`DB_PATH` must be a file path, not `:memory:` because SQLite operations use separate connections.
`POLLING_INTERVAL`, `TRANSACTION_THRESHOLD` and `REQUEST_TIMEOUT` must be positive, finite numbers
(the polling interval is an integer). The threshold is in native ETH; signal rules also retain
minimum amounts of 50 ETH for accumulation and 100 ETH for distribution.

`EXPLORER_PAGE_SIZE` (default 100) and `EXPLORER_MAX_PAGES` (default 1000) are
positive integers. Scans fix the end block, request ascending pages and deduplicate
transaction hashes. A short/empty page proves termination; repeated pages, invalid
ordering/bounds, API errors or an exhausted page budget retain the checkpoint.
Tune page size to your explorer service limits. The parameter contract is documented
by [Etherscan V2 txlist](https://docs.etherscan.io/api-reference/endpoint/txlist).

AI now defaults to **off**. Use `ENABLE_REASONING=true` and select `LLM_PROVIDER` explicitly to enable it.
`ENABLE_AI_REASONING` is a deprecated alias of `ENABLE_REASONING`;
`OLLAMA_BASE_URL` is a deprecated alias of `OLLAMA_URL`. Aliases warn and conflicting values fail.
Older templates contained both reasoning flags with contradictory values: remove the old name.
The provider default is Ollama (previously LMStudio). See `.env.example` for provider settings.
AI fallback providers may contact local endpoints and configured cloud services;
cloud services receive transaction/context data and may charge for requests.
Trading, SaaS, MongoDB, Redis and licensing variables in old templates are unused and should be removed.

## Exit codes

- `0`: demo/doctor/test succeeded, or `--once` completed with no new events.
- `10`: `--once` completed with new events (including events below the alert threshold).
- `2`: invalid configuration or CLI usage; no live clients/database initialized for invalid settings.
- `3`: runtime/service/scan/delivery failure, or doctor could not verify an enabled service.

`--once` also returns 3 while sends on enabled chains remain pending (including
retry delays, active leases or a changed recipient). Acquisition checkpoints may
already have committed. Continuous mode retries on subsequent cycles; each flush
claims at most 100 notifications, waits 5 seconds then doubles to at most one hour
after failure, and bounds each send by REQUEST_TIMEOUT. Changing chat/subscribers
leaves old messages bound to the old recipient; disabling notifications pauses them.

## SQLite migration and wallets

Back up an existing database before upgrading. Initialization adds recovery tables
and wallet metadata columns without rewriting old signals or delivery history.
Legacy markers cannot establish that old notifications were delivered.
The first runtime initialization seeds static examples once, preserving existing
wallet rows. Subsequent edits use WalletRegistry on the configured DB_PATH (README
example); removal/inactivation survives restart and each chain has independent metadata.
`threshold_override=None` follows TRANSACTION_THRESHOLD; legacy explicit thresholds
are retained. The stored use_default_threshold flag distinguishes inherited thresholds.
An updated registry is reloaded each cycle and gets a separate checkpoint scope;
unfinished previous ranges are retained and new wallets get the initial lookback.
Completed signals and queued messages retain their original content/settings.

`--doctor` checks chain ID/latest block, explorer block query, Telegram token/chat access,
and the selected local AI model listing. It never sends a message or requests inference.
Cloud AI and fallback providers remain operationally unverified; cloud AI yields code 3.
Telegram get_chat verifies access, not permission to send or guaranteed delivery.
All external checks use `REQUEST_TIMEOUT`; inference uses `REASONING_TIMEOUT`.
