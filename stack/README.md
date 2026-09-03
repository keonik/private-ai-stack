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
| Add or swap a backend (MLX server, llama.cpp, a cloud model) | add a block to `litellm/config.yaml`, then `docker compose up -d litellm` |
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

## Backends: oMLX and Ollama, both wired

`litellm/config.yaml` routes the friendly names (`local/chat`, `local/embed`, …)
to **oMLX** — an OpenAI-compatible MLX server on the Mac host, which also serves
vision, STT and TTS — and keeps **Ollama** under `ollama/*` as the Linux primary
and Mac fallback. Switching the whole stack from one to the other was the edit
of one file plus `docker compose up -d litellm`; Open WebUI, rag-ingest and the
extraction service never changed. That is the point of the router.

Measured through LiteLLM on an M4 Max, thinking off, 200 generated tokens:

| friendly name | backend · model | tok/s |
|---|---|---|
| `local/chat-small` | oMLX · gemma4-e4b (4B-class, VLM) | 89 |
| `ollama/chat-small` | Ollama · qwen3:4b | 127 |
| `local/chat` | oMLX · Qwen3.8-27B-4bit | 28 |
| `ollama/chat` | Ollama · qwen3:8b | 77 |

Read carefully: these are different models, not the same model on two engines.
The 27B is ~3× the parameters of the 8B and materially stronger on extraction
and grounded answers; 28 tok/s for a 27B on a laptop-class chip is the headline.
On the 4B row Ollama is faster, but it is a different architecture. A same-model
bake-off is the honest next measurement.

### Disabling "thinking" — the flag depends on the route

| route | `think: false` | `chat_template_kwargs: {enable_thinking: false}` | `/no_think` in prompt |
|---|---|---|---|
| LiteLLM → Ollama | **works** | ignored | ignored |
| LiteLLM → oMLX (OpenAI-compatible) | ignored | **works** | ignored |

Send both. Each backend ignores the other's flag, and LiteLLM forwards both.
The reranker in `../rag` does exactly this. Forgetting it does not error — it
silently burns the token budget on reasoning and returns an empty answer.

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

## `restart` does not re-read `.env`

`docker compose restart <svc>` reuses the existing container and its baked-in
environment. After changing `.env` (or anything under `environment:`), run
`docker compose up -d <svc>` — Compose sees the diff and recreates the
container. Symptom of getting this wrong: the config file says one thing, the
container does another, and you chase a "Connection refused" that is really an
old port number.

## Port collisions (read this on a machine that already runs things)

Compose binds host ports 3000, 4000, 8088. If one is taken — an ssh `-L` forward
or another service will do it — `docker compose up` fails for that one container
with *"port is already allocated"*, or worse, a `localhost` listener silently
shadows the container and health checks pass while requests go elsewhere.
Check with `lsof -nP -iTCP:<port> -sTCP:LISTEN`, then override in `.env`
(`WEBUI_PORT`, `LITELLM_PORT`, `RAG_PORT`). Every script reads `.env`, so nothing
else changes. Containers talk to each other by service name on the internal
network and are unaffected.

## Registering rag-ingest as a tool in Open WebUI

Add it under **Admin Panel → Settings → Tools**, URL `http://rag-ingest:8080`, path `openapi.json`, no auth.
Admin-level connections are fetched by the Open WebUI backend, so the compose service name resolves.
The per-user **Settings → Tools** dialog fetches the spec from your *browser*, which cannot resolve
`rag-ingest`; that path only works with a host-reachable URL such as `http://localhost:8088`.
Then in any chat, open the tools menu under the prompt and enable it.

## Scaling note: 500 form PDFs in one drop

Measured on the reference Mac (M4 Max, oMLX backend, embeddinggemma-300m) with one day of Ohio OH-1
traffic crash reports (public records, 4-5 text-layer pages each):

| | |
|---|---|
| files / chunks | 501 / 9,201 |
| ingest wall time | 5 min 1 s (~100 files/min, embed-bound) |
| re-run with nothing changed | 0.3 s (502 files skipped by content hash) |
| index on disk | 109 MB |
| hybrid search, k=5 | 0.66 s |
| grounded answer, 27B | 20-40 s |

Four changes in `rag-ingest/app.py` made that work:

- **Batch-aware indexing.** BM25 is rebuilt once per batch, not once per file, and LanceDB fragments are
  compacted after each batch. Per-file rebuilds were O(n²) at this size.
- **Content-hash skip.** Files whose bytes are already indexed are skipped; `POST /ingest?force=true`
  re-embeds everything. Without this the smoke test re-embedded 500 files on every run.
- **Polling watcher by default** (`WATCH_POLL=1`). Files written from the macOS side never generate inotify
  events inside a Docker Desktop bind mount, so the inotify watcher silently missed the entire drop.
- **Identifier lookup.** A query containing an ID-like token (`26-29237`, `INV-1042`) that matches a source
  filename pins that document's first chunks to the top. BM25 splits such tokens on punctuation and
  embeddings barely encode them, so "what happened in report 26-29237" previously missed the file entirely.

What chunk retrieval alone cannot do on this corpus: answer *field-level* questions ("which reports had a
suspected impaired driver"). Every OH-1 carries the same code legends, so the alcohol/drug text appears in all
501 documents and the model dutifully cites the legend. Questions about the free-text narrative (what was hit,
where, which agency) work well. The fix is structured extraction feeding filters, below.

## Extracted fields as filters (sidecars)

`extract/scripts/batch.py` writes a `<file>.meta.json` sidecar next to each document with the extracted
fields, per-field confidence and review flags. rag-ingest loads every sidecar in the inbox (on start, on
`POST /ingest`, and when the watcher sees one change) and exposes:

| endpoint | what it does |
|---|---|
| `GET /fields` | which fields exist, their types, example values. The model calls this first. |
| `GET /documents?filter={"animal_involved": true}` | exact list of matching documents, no retrieval, no LLM. Answers "which documents ..." questions. |
| `GET /search?q=...&filter={...}` / `POST /query {"filter": {...}}` | hybrid retrieval restricted to matching documents; hits carry their `fields`. |

Filter semantics: equality per key; strings match as case-insensitive substrings; keys look in `fields`
first, then the sidecar's top level (`review`, `model`). All three are in the OpenAPI spec, so a chat in
Open WebUI with the tool enabled will route "which reports involved a deer in county 67" to `/documents`
and "what happened in the Ravenna crash" to `/query`.

The sidecar contract is deliberately tiny so any extractor can produce one: `{"source": "<filename>",
"fields": {...}, "review": [...]}`.

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
