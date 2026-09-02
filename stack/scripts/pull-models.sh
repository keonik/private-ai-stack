#!/usr/bin/env bash
# Pull the default models into whichever Ollama this machine uses.
# Mac: host Ollama (must be running: `ollama serve` or the menu bar app).
# Linux profile: the container.
set -euo pipefail
MODELS=(${MODELS:-qwen3:8b qwen3:4b nomic-embed-text})
if docker compose ps --status running ollama >/dev/null 2>&1 && docker compose ps --status running ollama | grep -q ollama; then
  for m in "${MODELS[@]}"; do docker compose exec ollama ollama pull "$m"; done
elif command -v ollama >/dev/null 2>&1; then
  curl -sf localhost:11434/api/tags >/dev/null || { echo "host ollama not running; start it with: ollama serve" >&2; exit 1; }
  for m in "${MODELS[@]}"; do ollama pull "$m"; done
else
  echo "no ollama found (host or container)" >&2; exit 1
fi
echo "models ready: ${MODELS[*]}"
