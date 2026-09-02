# stack — one-command private LLM platform

Ollama · LiteLLM · Open WebUI · rag-ingest · Postgres, wired so that **every
request is keyed and audited** and **everything is backed up by a script**.

Runs on a Mac (Metal, via host Ollama) or a Linux box with a GPU (Ollama in a
container). Same compose file, two profiles.

## Run it

```bash
cp .env.example .env
# generate the three secrets:
for k in POSTGRES_PASSWORD LITELLM_MASTER_KEY WEBUI_SECRET_KEY; do echo "$k=$(openssl rand -hex 24)"; done

# Mac: Ollama on the host for Metal
brew install ollama && ollama serve &      # or the menu-bar app
./scripts/pull-models.sh                   # qwen3:8b, qwen3:4b, nomic-embed-text
docker compose up -d

# Linux with NVIDIA
docker compose --profile linux up -d && ./scripts/pull-models.sh

./scripts/healthcheck.sh
open http://localhost:3000                 # first signup = admin; then set ENABLE_SIGNUP=False
```

## Day-2

| Task | Command |
|---|---|
| Issue a key for a user/app with a budget | `./scripts/new-key.sh alice 20 60` |
| See who called what | LiteLLM UI at `http://localhost:4000/ui` (spend logs), or `psql` the `LiteLLM_SpendLogs` table |
| Add a backend (MLX server, llama.cpp, a cloud model) | add a block to `litellm/config.yaml`, `docker compose restart litellm` |
| Drop documents in | copy into `data/inbox/` — `rag-ingest` watches it; or `curl -X POST :8088/ingest` |
| Ask with citations | `curl -X POST :8088/query -H 'content-type: application/json' -d '{"question":"..."}'` |
| Let the chat UI use retrieval | Open WebUI → Admin → Tools → add OpenAPI server `http://rag-ingest:8080` |
| Backup / restore | `./scripts/backup.sh` → `backups/<ts>.tar.gz`; `./scripts/restore.sh backups/<ts>.tar.gz` |

## What's audited

- **Every model call** — LiteLLM writes key, model, tokens, cost, timestamp to Postgres.
- **Every retrieval** — `rag-ingest` appends `{user, query_hash, returned chunk ids}` to `data/audit.jsonl`. Query text is hashed, not stored.
- **Every ingest** — source filename, doc id, chunk count.

## Hardening checklist before it touches real data

- [ ] `ENABLE_SIGNUP=False` after the admin exists
- [ ] Replace the master key in Open WebUI with a per-app virtual key
- [ ] Put :3000 behind TLS (Caddy / your tunnel); don't expose :4000 or :8088 publicly
- [ ] Full-disk encryption on the host
- [ ] Run `backup.sh` on a schedule (restore round-trip verified: wiped index + audit log + DB all came back)
- [ ] Read `../docs/phi-pattern.md` if the documents are regulated

## Why these pieces

- **LiteLLM** as the single choke point: keys, budgets, audit, and backend
  swapping without touching clients. This is what makes the stack operable
  rather than a demo.
- **Ollama** because it is what buyers already run. `mlx/chat` is wired as an
  optional route for Apple Silicon, where an MLX server is noticeably faster on
  the same hardware.
- **rag-ingest** is deliberately small: hybrid BM25 + vector with RRF, page-level
  citations, an OpenAPI spec so the UI can call it as a tool. The full reference
  build with reranking and an eval harness is in [`../rag/`](../rag/).

## Verify

```bash
./scripts/smoke.sh      # health → models → embed → chat → ingest → search → cited answer → audit → spend log
```

`data/inbox/sample-msa.txt` is a synthetic contract so the smoke test has
something to retrieve on a fresh install. CI runs the same script on a CPU-only
runner with `qwen3:0.6b` (see `.github/workflows/stack-smoke.yml`).

## TLS

`Caddyfile.example` fronts the UI with automatic HTTPS. Keep :4000 and :8088 off
the public interface; apps authenticate to LiteLLM with virtual keys.

## Port collisions (read this on a machine that already runs things)

Compose binds host ports 3000, 4000, 8088. If one is taken — an ssh `-L` forward
or another service will do it — `docker compose up` fails for that one container
with *"port is already allocated"*, or worse, a `localhost` listener silently
shadows the container and health checks pass while requests go elsewhere.
Check with `lsof -nP -iTCP:<port> -sTCP:LISTEN`, then override in `.env`
(`WEBUI_PORT`, `LITELLM_PORT`, `RAG_PORT`). Every script reads `.env`, so nothing
else changes. Containers talk to each other by service name on the internal
network and are unaffected.

## Thinking models

`qwen3:*` (and DeepSeek-R1-style models) emit a reasoning block before the
answer. Through LiteLLM it arrives as `reasoning_content`, and `content` is
empty if `max_tokens` is small. Two things learned the hard way:

- **`"think": false` in the request body works** end to end (LiteLLM → Ollama)
  and returns the bare answer in a handful of tokens. Use it for scoring,
  classification, extraction, and anything with a tight token budget.
- **A `/no_think` suffix in the prompt does not work** through this route. It
  looks like it should; it doesn't. The RAG answer path leaves the budget open,
  so it is unaffected either way.
