# Troubleshooting

Start with `.venv/bin/python main.py --demo`, then `--doctor` after live configuration.

| Symptom | Action |
| --- | --- |
| CONFIG ERROR / code 2 | Fix the named variable locally; never paste credentials. Complete URLs are required. |
| AI alias conflict | Remove ENABLE_AI_REASONING / OLLAMA_BASE_URL; keep ENABLE_REASONING / OLLAMA_URL. |
| No Telegram alerts | Check doctor; start the bot conversation / grant chat access. Empty scans and insignificant events send no alert. |
| Scan FAILED / code 3 | Check explorer access/rate limits, RPC chain ID and connectivity. Failed/incomplete scans retain the chain cursor. |
| Full explorer page | Pagination is not implemented; the scan fails conservatively at 100 results. |
| Saved signal but delivery failed | Check destination permissions; early transaction marking means retry is not guaranteed. |
| AI model unavailable | Keep AI off initially, or install the configured model via the explicit AI profile. |
| Cloud AI doctor unverified | Doctor intentionally makes no inference calls and cannot certify cloud readiness. |
| ensurepip unavailable | Install the Python 3.12 venv package or follow README's uv alternative. |
| Docker unavailable in WSL | Enable Docker Desktop integration for this distribution; re-run docker version. |
| Test failure | Reproduce in a fresh Python 3.12 venv and classify code/dependency/environment causes from the traceback. |

No startup message is sent. Token verification does not prove permission to send to a destination.
For real synthetic test messages use `--test` / `--preview` only with an authorized chat.
Logs report external failure types without provider exception bodies, which may contain keys/URLs.
