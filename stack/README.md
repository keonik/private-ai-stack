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
| Drop documents in | copy into `data/inbox/` — `rag-ingest` watches it; or `curl -X POST :8080/ingest` |
| Ask with citations | `curl -X POST :8080/query -H 'content-type: application/json' -d '{"question":"..."}'` |
| Let the chat UI use retrieval | Open WebUI → Admin → Tools → add OpenAPI server `http://rag-ingest:8080` |
| Backup / restore | `./scripts/backup.sh` → `backups/<ts>.tar.gz`; `./scripts/restore.sh backups/<ts>.tar.gz` |

## What's audited

- **Every model call** — LiteLLM writes key, model, tokens, cost, timestamp to Postgres.
- **Every retrieval** — `rag-ingest` appends `{user, query_hash, returned chunk ids}` to `data/audit.jsonl`. Query text is hashed, not stored.
- **Every ingest** — source filename, doc id, chunk count.

## Hardening checklist before it touches real data

- [ ] `ENABLE_SIGNUP=False` after the admin exists
- [ ] Replace the master key in Open WebUI with a per-app virtual key
- [ ] Put :3000 behind TLS (Caddy / your tunnel); don't expose :4000 or :8080 publicly
- [ ] Full-disk encryption on the host
- [ ] Run `backup.sh` on a schedule and test `restore.sh` once
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
