# SentryCage project instructions

The internal pilot plan, when present locally, is PIANO_REFACTOR.md. README is the canonical user path;
docs/configuration.md and docs/architecture.md define configuration and recovery limits.

- Python 3.12 is the verified target. Preserve the existing modules; use the shared
  raw transaction → analysis → signal → SQLite → recipient pipeline for demo and live.
- Validate settings before clients/storage. Parse CLI modes before initialization.
- Demo must not load .env, read credentials, construct RPC/Telegram/LLM clients, access
  network or touch the live database. Use real analysis, generation, persistence and formatting.
- AI off means zero provider construction or calls. Notifications off must be explicit.
- Never read/modify an operator's .env in agent work or expose credentials in errors/logs/tests.
- Do not send real Telegram messages without explicit authorization for the destination.
- Run pytest before and after each logical increment; correct failures before continuing.
  Test configuration/startup, real offline pipeline, delivery errors, network-free demo,
  diagnostics, acquisition failures and cursor retention. Use temporary file SQLite databases.
- Keep PIANO_REFACTOR.md current: only check verified items, record actual commands/results,
  and leave external blockers open. Never claim live/container validation from mocks/static checks.
- Do not solve checkpoint/outbox/pagination limits by moving mark_tx_processed after send.
  They need separately defined recovery/duplicate semantics.
- Recovery now commits signals/outbox/local completion atomically before sending; checkpoints
  advance after local completeness, with delivery tracked separately. Preserve leases, recipient
  binding and the documented duplicate ambiguity. Keep tracker/analyzer on the shared SQLite
  wallet snapshot; static entries are seed-once examples. Preserve AI provenance and full references.
- Preserve pre-existing edits and permissions. Keep internal evidence, runtime data and secrets
  out of commits and Docker context. Use explicit file pathspecs for local commits.
- LOCAL_ONLY: no remotes, push, PR, release or repository settings changes without explicit
  authorization establishing destination, visibility and scope. Existing remotes/publication
  grant no authorization. Review the inventory/diff/secrets before any authorized publication.

The root workspace instructions also apply. Historical CLAUDE.md / Claude.md status entries
are not evidence of current runtime verification; these instructions govern the current pilot.
