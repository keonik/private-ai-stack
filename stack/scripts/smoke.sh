#!/usr/bin/env bash
# End-to-end smoke test: every service healthy, a doc ingested, retrieval returns it with a page cite,
# an answer comes back through LiteLLM, and the audit log recorded it. Exit non-zero on any failure.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
LL=http://localhost:${LITELLM_PORT:-4000}; RAG=http://localhost:${RAG_PORT:-8088}; UI=http://localhost:${WEBUI_PORT:-3000}
H="Authorization: Bearer ${LITELLM_MASTER_KEY}"; fail=0
step() { printf "\n▶ %s\n" "$*"; }
ok()   { printf "  ✓ %s\n" "$*"; }
bad()  { printf "  ✗ %s\n" "$*"; fail=1; }

step "services"
for pair in "litellm|$LL/health/liveliness" "rag-ingest|$RAG/health" "open-webui|$UI/health"; do
  n=${pair%%|*}; u=${pair#*|}
  curl -sf -m 10 "$u" >/dev/null && ok "$n" || bad "$n ($u)"
done

step "models visible through litellm"
models=$(curl -sf -m 15 -H "$H" "$LL/v1/models" | python3 -c 'import sys,json; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null)
[ -n "$models" ] && ok "$models" || bad "no models"

step "embedding round-trip"
dim=$(curl -sf -m 60 -H "$H" -H 'content-type: application/json' "$LL/v1/embeddings" -d "{\"model\":\"${EMBED_MODEL:-local/embed}\",\"input\":\"hello\"}" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["data"][0]["embedding"]))' 2>/dev/null)
[ -n "$dim" ] && ok "dim=$dim" || bad "embeddings failed"

step "chat round-trip"
reply=$(curl -sf -m 300 -H "$H" -H 'content-type: application/json' "$LL/v1/chat/completions" -d "{\"model\":\"${CHAT_MODEL:-local/chat}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: pong /no_think\"}],\"max_tokens\":64,\"temperature\":0}" | python3 -c "import sys,json; m=json.load(sys.stdin)['choices'][0]['message']; print((m.get('content') or m.get('reasoning_content') or '').strip())" 2>/dev/null)
[ -n "$reply" ] && ok "reply: ${reply:0:40}" || bad "chat failed"

step "ingest inbox"
n=$(curl -sf -m 600 -X POST "$RAG/ingest" | python3 -c 'import sys,json; print(json.load(sys.stdin)["chunks_indexed"])' 2>/dev/null)
[ "${n:-0}" -gt 0 ] && ok "$n chunks" || bad "ingest returned ${n:-nothing} (is data/inbox/ populated?)"

step "hybrid search with citation"
hit=$(curl -sf -m 60 "$RAG/search?q=notice+period+for+termination&k=3" -H "X-User: smoke" | python3 -c "import sys,json; h=json.load(sys.stdin)['hits']; print(h[0]['source'], 'p.%s'%h[0]['page'], 'score=%s'%h[0]['score']) if h else print('')" 2>/dev/null)
[ -n "$hit" ] && ok "$hit" || bad "no hits"

step "grounded answer"
ans=$(curl -sf -m 300 -X POST "$RAG/query" -H 'content-type: application/json' -H "X-User: smoke" -d '{"question":"How many days notice is required to terminate the agreement?","k":4}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("answer") or "")[:160].replace("\n"," "))' 2>/dev/null)
[ -n "$ans" ] && ok "$ans" || bad "no answer"
echo "$ans" | grep -qi "30" && ok "answer mentions 30 (expected)" || bad "answer did not mention 30"

step "audit trail"
c=$(grep -c '"event": "query"' data/audit.jsonl 2>/dev/null || echo 0)
[ "$c" -gt 0 ] && ok "$c query events in data/audit.jsonl" || bad "audit log empty"

step "spend log in postgres"
rows=$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-litellm}" "${POSTGRES_DB:-litellm}" -tAc 'select count(*) from "LiteLLM_SpendLogs"' 2>/dev/null | tr -d ' ')
[ "${rows:-0}" -gt 0 ] && ok "$rows requests logged" || bad "no spend logs (table may not exist yet)"

echo; [ $fail = 0 ] && echo "SMOKE: PASS" || echo "SMOKE: FAIL"; exit $fail
