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

`--doctor` checks chain ID/latest block, explorer block query, Telegram token/chat access,
and the selected local AI model listing. It never sends a message or requests inference.
Cloud AI and fallback providers remain operationally unverified; cloud AI yields code 3.
Telegram get_chat verifies access, not permission to send or guaranteed delivery.
All external checks use `REQUEST_TIMEOUT`; inference uses `REASONING_TIMEOUT`.
