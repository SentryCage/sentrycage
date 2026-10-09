#!/usr/bin/env bash
# Build only by default; AI model download requires --ai. Never edit .env.
set -euo pipefail
project_dir="$(dirname "$(dirname "$(realpath "$0")")")"
case "${1:-}" in
  "") ai=false ;;
  --ai) ai=true ;;
  *) echo "Usage: bash scripts/setup.sh [--ai]" >&2; exit 2 ;;
esac
docker compose version
docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml" build sentrycage
if "$ai"; then
  docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml" --profile ai up -d ollama
  docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml" --profile ai run --rm ollama-init
fi
echo "Build complete. Configure .env locally, then follow README for doctor and startup."
