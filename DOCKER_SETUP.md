# Docker / Compose v2

The canonical local demo and live configuration steps are in [README](README.md).
Docker must be running, with WSL integration enabled when applicable.

## Credential-free demo

```bash
docker build -t sentrycage:pilot .
docker run --rm --network none sentrycage:pilot python main.py --demo
```

The direct image command needs no `.env` and uses a temporary database.
Building downloads dependencies; running the demo needs no network.

## Base monitoring

Configure `.env` yourself as described in README; keep `ENABLE_REASONING=false`.
The base Compose starts only SentryCage, has no AI model downloads, and exposes no ports.

```bash
bash scripts/setup.sh
docker compose run --rm sentrycage python main.py --doctor
docker compose up -d sentrycage
docker compose logs -f sentrycage
```

No container healthcheck is declared: the old check merely rewrote a database and did not
measure monitoring health. Use doctor, scan logs and `--once` for diagnosis; doctor is a
point-in-time check and does not certify continuous operation.

## Optional AI profile

Set `ENABLE_REASONING=true`, `LLM_PROVIDER=ollama`, `OLLAMA_URL=http://ollama:11434`
and `OLLAMA_MODEL` in your local `.env`. This opt-in step downloads the model.

```bash
bash scripts/setup.sh --ai
docker compose --profile ai run --rm sentrycage python main.py --doctor
docker compose --profile ai up -d sentrycage
```

Initialization targets the Ollama server with `OLLAMA_HOST`; readiness is limited to about
120 seconds and readiness plus model pull to 600 seconds. No Ollama host port is exposed.
If initialization times out, inspect `docker compose --profile ai logs ollama`, then retry.

## Persistence check (when Docker is available)

```bash
docker compose run --rm --no-deps sentrycage python -c "from src.database.sqlite import init_db, set_config; init_db(); set_config('persistence_probe', 'pilot')"
docker compose up -d --force-recreate sentrycage
docker compose run --rm --no-deps sentrycage python -c "from src.database.sqlite import get_config; assert get_config('persistence_probe') == 'pilot'"
```

`/app/data` uses the named `sentrycage-data` volume. Recreating containers or `docker compose down`
keeps it; `docker compose down -v` deletes volumes and their data. The probe writes a test key.
Live startup sends alerts; only run it with the destination authorized for messages.

Local validation on 2026-10-09: YAML and shell checks only. Build, network-free container demo,
AI profile and volume recreation remain unverified because Docker/WSL is unavailable.
