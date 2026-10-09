# Troubleshooting

Start with `.venv/bin/python main.py --demo`, then `--doctor` after live configuration.

| Symptom | Action |
| --- | --- |
| CONFIG ERROR / code 2 | Fix the named variable locally; never paste credentials. Complete URLs are required. |
| AI alias conflict | Remove ENABLE_AI_REASONING / OLLAMA_BASE_URL; keep ENABLE_REASONING / OLLAMA_URL. |
| No Telegram alerts | Check doctor; start the bot conversation / grant chat access. Empty scans and insignificant events send no alert. |
| FAILED / code 3 | Check services and pending notifications. Incomplete acquisition retains its checkpoint; locally completed scans can advance while delivery waits. |
| Page budget / repeated page | Check service pagination; tune EXPLORER_PAGE_SIZE / EXPLORER_MAX_PAGES to service limits. The incomplete range retains its checkpoint. |
| Saved signal but delivery failed | The immutable message remains queued. Check destination permissions and allow the scheduled retry; ambiguous sends may duplicate. |
| Chat changed / old queue pending | Old messages stay bound to the original destination. Restore the intended destination to drain them; notifications off pauses the queue. |
| RPC block precedes checkpoint | Investigate endpoint lag or reorganization; the program will not silently rewind the checkpoint. |
| Wallet edits do not apply | Edit WalletRegistry in the same DB_PATH used by the tracker. Static files seed fresh databases only. Completed signals remain unchanged. |
| AI output uses rules instead | Inspect saved provenance/fallback_reason: no provider, timeout, circuit breaker, invalid output or provider error. |
| AI model unavailable | Keep AI off initially, or install the configured model via the explicit AI profile. |
| Cloud AI doctor unverified | Doctor intentionally makes no inference calls and cannot certify cloud readiness. |
| ensurepip unavailable | Install the Python 3.12 venv package or follow README's uv alternative. |
| Docker unavailable in WSL | Enable Docker Desktop integration for this distribution; re-run docker version. |
| Test failure | Reproduce in a fresh Python 3.12 venv and classify code/dependency/environment causes from the traceback. |

No startup message is sent. Token verification does not prove permission to send to a destination.
For real synthetic test messages use `--test` / `--preview` only with an authorized chat.
Logs report external failure types without provider exception bodies, which may contain keys/URLs.
