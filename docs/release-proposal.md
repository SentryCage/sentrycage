# Publication proposal — for review only

Nothing in this file authorizes publication. The candidate is local and unpublished.
Destination, visibility, branch and exact content must be explicitly authorized before any push,
release or repository settings change. Do not include internal plan/delivery evidence or runtime files.

## Proposed About

Self-hosted Ethereum/Arbitrum wallet monitoring with SQLite, Telegram alerts and a credential-free offline demo. Optional AI; monitoring and recovery limits documented.

## Proposed topics

`ethereum`, `arbitrum`, `onchain-analysis`, `wallet-monitoring`, `telegram`, `sqlite`, `self-hosted`, `python`

## Proposed pilot release notes

Try the offline demo from README. This pilot validates configuration before startup and uses a shared
transaction-to-signal-to-SQLite-to-recipient pipeline. Telegram destinations and outcomes are explicit;
AI is disabled by default. Doctor performs read-only checks and once runs a single scan.

Configuration migration and operational limits are in the changelog/configuration guide.
The synthetic demo and mocked integration are verified on Python 3.12.3. Live services and container
runtime remain unverified; this candidate does not certify complete monitoring or guaranteed delivery.
Before stable release, define persistent checkpoints, explorer pagination, Telegram outbox/retry and
known duplicate behavior, and audit AI attribution and persistence.
