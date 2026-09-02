#!/usr/bin/env bash
# One-screen status of every service. Exit non-zero if anything is down.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
ok=0
chk() { local name=$1 url=$2; shift 2
  if curl -sf -m 5 "$@" "$url" >/dev/null; then printf "  %-12s ok    %s\n" "$name" "$url"; else printf "  %-12s DOWN  %s\n" "$name" "$url"; ok=1; fi; }
echo "services:"
chk ollama    "${OLLAMA_HOST_URL:-http://localhost:11434}/api/tags"
chk litellm   "http://localhost:${LITELLM_PORT:-4000}/health/liveliness"
chk rag       "http://localhost:${RAG_PORT:-8088}/health"
chk webui     "http://localhost:${WEBUI_PORT:-3000}/health"
echo "models via litellm:"
curl -sf -m 10 -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-}" "http://localhost:${LITELLM_PORT:-4000}/v1/models" \
  | python3 -c 'import sys,json; [print("  -",m["id"]) for m in json.load(sys.stdin).get("data",[])]' 2>/dev/null || { echo "  (could not list)"; ok=1; }
exit $ok
