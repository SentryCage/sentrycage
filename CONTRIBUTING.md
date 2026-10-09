# Contributing to SentryCage

Use [README](README.md) to install Python 3.12 dependencies and run the offline demo.
No credentials are required for development tests. Read [AGENTS.md](AGENTS.md) and
[architecture](docs/architecture.md) before changing pipeline behavior.

Run `.venv/bin/python -m pytest tests/ -q` before and after each logical change.
Add meaningful regressions for changed behavior, keep external clients mocked, and update
configuration/documentation when behavior changes. For coverage use the README command.
Lint is `flake8 --config=.flake8 src/ main.py` after installing `flake8==7.1.1`.

Use [GitHub Issues](https://github.com/SentryCage/sentrycage/issues) for bugs, questions and proposals.
Include reproduction steps, sanitized error types, Python/OS version and mode used.
Never include `.env`, keys, tokens, databases or private logs. Contributions are MIT licensed.
Publishing code, opening a PR or changing repository settings requires explicit project/destination
visibility/content authorization under the local publication policy.
