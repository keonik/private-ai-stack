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
| Backup / restore | `./scripts/backup.sh` → `backups/<ts>.tar.gz` (keeps the newest `KEEP=7`); `./scripts/restore.sh backups/<ts>.tar.gz` |
| Nightly backup + auto-restart after Docker restarts | `./scripts/install-launchd.sh` (macOS; see "Keeping it up") |
| Remove a document | delete it from `data/inbox/` — its chunks and sidecar go with it (see "Removing documents") |
| Restrict which users see which documents | `data/acl.json` + three headers on the tool-server connection (see "Per-group document scoping") |
| Measure retrieval on your own corpus | `python3 scripts/rag_eval.py` (see "Retrieval evaluation") |
| See what the stack is doing: uptime, latency, tokens, spend, containers, logs, alerts | `docker compose --profile obs up -d` → Grafana at :3001 (see "Observability") |

## What's audited

- **Every model call** — LiteLLM writes key, model, tokens, cost, timestamp to Postgres.
- **Every retrieval** — `rag-ingest` appends `{user, query_hash, returned chunk ids}` to `data/audit.jsonl`. Query text is hashed, not stored.
- **Every ingest** — source filename, doc id, chunk count.

## Hardening checklist before it touches real data

- [ ] `ENABLE_SIGNUP=False` after the admin exists
- [ ] Replace the master key in Open WebUI with a per-app virtual key
- [ ] Put :3000 behind TLS (Caddy / your tunnel); don't expose :4000 or :8088 publicly
- [ ] Full-disk encryption on the host
- [x] Run `backup.sh` on a schedule (`install-launchd.sh`: 03:15 nightly, 7 kept; restore round-trip verified: wiped index + audit log + DB all came back)
- [ ] Identity in front of the tunnel (Cloudflare Access or equivalent) so the login form is not the only thing between the internet and the documents
- [ ] `data/acl.json` if more than one group of people will use the chat
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

## Removing documents

Delete the file from `data/inbox/`. The watcher sees the removal (polling, so it works on macOS bind
mounts too) and drops every chunk with that source name; the sidecar, if any, is forgotten on its next
reload. Re-saving a file with new content replaces its old chunks rather than adding to them. `POST /ingest`
also reconciles: anything indexed whose file is gone is removed, and the same sweep runs at startup, so a
file deleted while the service was down is still cleaned up. Each removal is audited (`remove`, with
`reason: deleted|missing`).

Verified: add a text file with a made-up word → it is the top hit in ~5 s; delete it → gone in ~5 s;
overwrite a file → only the new wording is found.

## Per-group document scoping

Single-tenant by default: with no `data/acl.json` every caller sees every document. Create the file and
the service scopes `/search`, `/query`, `/documents` and `/fields` by the caller's Open WebUI groups:

```json
{"admin_all": true,
 "default": [],
 "groups": {"claims": ["oh1-*"], "legal": ["*-msa.txt", "contracts/*"]}}
```

Globs match source filenames, case-insensitively. A caller's allowed set is `default` plus the patterns
of every group they belong to; admins see everything unless `admin_all` is false. Filters and ACL
compose: a filtered `/documents` call only lists matches the caller may see, and `/fields` only shows
example values from those documents. The file is re-read whenever its mtime changes.

Who is calling comes from headers Open WebUI adds to the connection. Admin Panel → Settings → Tools →
the rag-ingest connection → Headers:

```
X-User-Id: {{USER_ID}}
X-User-Role: {{USER_ROLE}}
X-User-Groups: {{USER_GROUPS}}
```

These placeholders are filled server-side per request (Open WebUI 0.11: `utils/headers.py`), with no
env change; `ENABLE_FORWARD_USER_INFO_HEADERS` is not needed. Groups are Admin Panel → Users → Groups.
The headers are unsigned, which is fine on the compose network where only Open WebUI can reach
`rag-ingest:8080`; do not publish :8088. Calls with no headers (curl from the host) get `default` only,
so pass `X-User-Role: admin` for operator use.

Verified with a temporary ACL: no headers → nothing; `legal` → only the MSA; `claims` → the 501 crash
reports and 30 animal matches; `admin` → everything; file removed → unrestricted again.

## Keeping it up

`./scripts/install-launchd.sh` installs three user agents (templates in `launchd/`):

- `dev.private-ai-stack.backup` — `backup.sh` at 03:15, keeps 7 tarballs (`KEEP`). One run of the
  current data set is ~500 MB. Log: `~/Library/Logs/private-ai-stack.backup.log`.
- `dev.private-ai-stack.keepalive` — every 5 min, `docker compose up -d` if any service is not
  running. It does nothing when Docker itself is down (stopping Docker is a person's decision, not
  a script's). Verified by stopping a service by hand: back within one tick.
- `dev.private-ai-stack.docker-boot` — once at login: if the Docker engine is unreachable it runs
  `colima start` (Homebrew's colima is not a login service by default, so nothing came back after a
  reboot), waits for the engine, then brings the stack up. It only ever starts the engine when it is
  already down, so a deliberate `colima stop` during the day stays stopped. On a Docker Desktop machine
  it says so and exits rather than guessing.

Note that the engine is shared with anything else on the machine that uses Docker, so this agent starts
that too. `./scripts/install-launchd.sh remove` uninstalls all three.

## Retrieval evaluation

`scripts/rag_eval.py` builds a golden set from the sidecars — one question per sampled document,
written by `local/chat-small` from that document's summary with the report number withheld — caches
it under `data/eval/`, then asks `/search` in each mode and checks whether the target document is in
the top k (ranked by document, not chunk). 60 questions per set, 501-document corpus, k = 10:

| mode | recall@1 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| vector only | 0.47 / 0.57 | 0.55 / 0.72 | 0.62 / 0.75 | 0.51 / 0.63 |
| BM25 only | 0.62 / 0.63 | 0.87 / 0.87 | 0.90 / 0.90 | 0.71 / 0.74 |
| hybrid, lexical weight 1 | 0.60 / 0.60 | 0.77 / 0.78 | 0.88 / 0.92 | 0.67 / 0.70 |
| hybrid, lexical weight 3 (now default) | 0.65 / 0.65 | 0.82 / 0.83 | 0.88 / 0.90 | 0.73 / 0.73 |

Two numbers per cell: the set the weight was chosen on (seed 0) / a second set generated afterwards
(seed 1). Median search latency is ~70 ms in every mode.

What it says: on a corpus of 501 near-identical forms from the same day, what tells documents apart is
proper nouns — street names, townships, "parked trailers" — and BM25 is built for exactly that, while
embeddings blur "a Columbus rear-end collision" into a hundred neighbours. Plain reciprocal-rank fusion
let the weak vector list drag the good lexical list down. Weighting the lexical list 3× (`BM25_WEIGHT`,
or `bm25_weight=` per call) recovers BM25's recall while keeping the vector list for paraphrases, and
the second set shows it was not fitted to the first. On the prose corpus in `../rag` the same knob
should sit nearer 1; it is an env var for that reason, not a constant.

The ceiling is real, too: many questions ("Columbus collision Unit 1 Unit 2") are genuinely ambiguous
among same-day Columbus crashes, so ~0.9 recall@10 is close to what the questions allow, and the
misses are the same handful of generic questions in every mode.

## Observability

`docker compose --profile obs up -d` adds seven small containers and two provisioned dashboards. Nothing
in the main stack changes except one line in `litellm/config.yaml` (`callbacks: ["prometheus"]`) and a
`/metrics` endpoint on rag-ingest. Grafana is at `:3001` (`GRAFANA_USER` / `GRAFANA_PASSWORD`).

Setting `COMPOSE_PROFILES=obs` in `.env` makes it permanent: plain `docker compose up -d` and the
keepalive agent then include these services, so they come back with everything else after a reboot.

| piece | what it gives you | where it comes from |
|---|---|---|
| Prometheus | 30 days of metrics | scrapes LiteLLM `/metrics/` (with the master key), rag-ingest, the docker-stats exporter, blackbox, itself |
| blackbox | **is it up** — one probe per service every 15 s, including oMLX on the host | `/health`-style URLs; a 401 from oMLX still counts as up |
| docker-stats | CPU %, memory, network, restarts per container | 120 lines of stdlib Python over the Docker socket (`observability/docker-stats/exporter.py`) |
| Loki + Alloy | every container's stdout/stderr, 14 days, searchable by service | Alloy discovers compose containers over the socket |
| Grafana | the dashboards and alerts below (port 3001 is open on the LAN like the chat UI, so give `GRAFANA_PASSWORD` a real value); also reads the LiteLLM spend table straight from Postgres | provisioned from `observability/grafana/` — nothing is clicked together |
| alert-relay | turns Grafana's alert JSON into a notification a person can read on a phone | 90 lines of stdlib Python (`observability/alert-relay/relay.py`) |

**Dashboards** (`observability/grafana/build_dashboards.py` generates the JSON; edit the Python, not the JSON):

- *Overview* — up/down tiles; requests per minute, p50/p95 latency, tokens per minute and failures by model
  from LiteLLM's live counters; **time to first token** per model, requests per hour, spend per day and a
  who-used-what table from the spend log; CPU, memory and network per container; an error/warning log
  panel across the stack.
- *RAG service* — index size, ingest errors, search/query p95, requests by endpoint, embedding latency
  (a proxy for "is oMLX busy"), hits per search (drops to 0 when a filter or ACL excludes everything),
  files indexed / skipped / removed, rag-ingest logs.

**Alerts** (Grafana-managed, provisioned in `provisioning/alerting/rules.yml`, evaluated every minute):

| alert | fires when |
|---|---|
| Service down | any probe fails for 2 min |
| Model requests failing | more than 3 failed model calls in 10 min |
| Chat p95 latency over 2 minutes | sustained 10 min |
| Document ingest errors | any file fails to index |
| Container restarting | more than 2 restarts in an hour |
| Container memory above 85 % of the Docker VM | for 5 min (that is the colima / Docker Desktop VM, not the Mac) |

All six go to one contact point, which posts to **alert-relay**. Grafana's own webhook payload is a JSON
envelope that a phone would display verbatim, so the relay unwraps it and sends one short notification per
alert: title `Service down - rag-ingest`, the summary as the body, `critical` mapped to a high priority and
a tap-through link to the alert in Grafana. Resolutions arrive as `Resolved: ...` at a lower priority.

Point it somewhere by setting one variable in `.env`:

```
NTFY_URL=https://ntfy.sh/<a long random topic name>     # then subscribe to that topic in the ntfy app
```

The topic name is the only secret, so make it long and random; ntfy is free and needs no account. A Slack
or Discord webhook URL works in the same variable — the relay picks the payload shape from the host name.
Unset, alerts still appear in Grafana and the relay just logs them. To bypass the relay entirely and send
Grafana's raw JSON somewhere, set `ALERT_WEBHOOK_URL` instead.

Verified end to end on the reference Mac: `docker compose stop rag-ingest` → `Service down - rag-ingest`
at priority 5 on an ntfy topic, with the summary as the body; `docker compose start` → `Resolved: Service
down - rag-ingest` at priority 2. Failure to phone takes the rule's 2-minute `for` plus up to a minute of
evaluation and 30 s of grouping, so 2-4 minutes depending on where the failure falls in that cycle. Every
dashboard query and alert expression was also run against the live datasources before being committed
(30 PromQL, 4 SQL, 6 rules).

Why not cAdvisor: Docker 29 with the containerd image store (`docker info` → `overlayfs [driver-type
io.containerd.snapshotter.v1]`, the colima default) leaves cAdvisor unable to identify containers
("failed to identify the read-write layer ID"), so it reports nothing per container. The stdlib
exporter reads the same numbers `docker stats` shows and does not care which storage driver is in use.

Cost on the box: the six containers idle at ~1 % CPU and ~270 MB of the VM's memory combined.

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
