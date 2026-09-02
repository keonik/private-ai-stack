#!/usr/bin/env bash
# Issue a LiteLLM virtual key for one user or app, with a budget and rate limit.
#   ./scripts/new-key.sh alice 20 60      # alias, max_budget USD, rpm
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
ALIAS=${1:?alias}; BUDGET=${2:-10}; RPM=${3:-60}
curl -sf "http://localhost:${LITELLM_PORT:-4000}/key/generate" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" -H "Content-Type: application/json" \
  -d "{\"key_alias\":\"${ALIAS}\",\"max_budget\":${BUDGET},\"rpm_limit\":${RPM},\"models\":[\"local/chat\",\"local/chat-small\",\"local/embed\"]}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["key"])'
