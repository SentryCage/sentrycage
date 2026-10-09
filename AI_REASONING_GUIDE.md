# Optional AI reasoning

AI is disabled by default. Start with the [README demo](README.md#run-the-demo).
Provider settings and migration aliases are documented in [configuration](docs/configuration.md);
Ollama's opt-in model download is documented in [Docker setup](DOCKER_SETUP.md#optional-ai-profile).

When enabled, the existing reasoning engine refines low-confidence interpretations, gathers market
context and retains bounded in-memory history. It supports Ollama, LMStudio, Claude and OpenAI,
with fallbacks. This is interpretation, not a verified forecast or a trading instruction.
Cloud inference can incur charges and transmits transaction/context data outside the host.
`--doctor` performs no inference and reports cloud/fallback readiness as unverified.

The local scripts `scripts/test_llm.py` and `scripts/benchmark_providers.py` perform real inference:
they are optional developer tools, require configured providers and may make paid requests.
They are not included in the runtime container. They are not part of the offline demo or certification.
