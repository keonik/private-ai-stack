# Architecture

```
                 ┌──────────────────────────────────────────────────────┐
  browser ──────▶│  Open WebUI  :3000  (auth, chat, RAG tool calls)      │
                 └───────────────┬──────────────────────────────────────┘
                                 │ OpenAI-compatible, one virtual key per user
                                 ▼
                 ┌──────────────────────────────────────────────────────┐
  apps/curl ────▶│  LiteLLM  :4000  (router · virtual keys · audit log)  │
                 └───┬───────────────┬───────────────────┬──────────────┘
                     │               │                   │
          ollama/*   │    openai/*   │       embeddings  │
                     ▼               ▼                   ▼
            ┌──────────────┐  ┌──────────────┐   ┌──────────────────┐
            │ Ollama       │  │ oMLX / other │   │ rag-ingest :8088 │
            │ host (Mac)   │  │ OpenAI-compat│   │ watch · chunk ·  │
            │ or container │  │ (optional)   │   │ embed · hybrid   │
            │ (Linux GPU)  │  └──────────────┘   │ search · cite    │
            └──────────────┘                     └────────┬─────────┘
                                                          │ LanceDB (file)
                 ┌──────────────────────────────────────────────────────┐
                 │  Postgres  (LiteLLM keys + every request logged)      │
                 └──────────────────────────────────────────────────────┘
```

## Why this shape

**One choke point.** Open WebUI and every app talk to LiteLLM only. LiteLLM is
where keys are issued, budgets enforced, and every request written to Postgres.
If a model backend changes, nothing upstream notices.

**Backends are pluggable, and that was tested.** The stack was built on Ollama,
then switched to an oMLX server (OpenAI-compatible, MLX, on the Mac host) by
editing `litellm/config.yaml` and recreating the router. No client changed.
`ollama/*` stays wired as the Linux primary and Mac fallback, because Ollama is
what most buyers run.

**Mac vs Linux is a compose profile, not a fork.** On a Mac, Ollama must run on
the host to reach Metal; containers reach it at `host.docker.internal`. On
Linux, `--profile linux` starts Ollama in a container with GPU reservation.
Same file.

**RAG is a service with an OpenAPI spec.** Open WebUI can register
`rag-ingest` as a tool server, so retrieval is a tool call the model makes, and
results carry document + page citations back into the chat.

**State lives in three places.** Postgres (keys, audit), Open WebUI's volume
(users, chats), LanceDB (index). `scripts/backup.sh` captures all three.

## Ports

| Service | Port | Purpose |
|---|---|---|
| open-webui | 3000 | UI |
| litellm | 4000 | OpenAI-compatible API + admin UI at `/ui` |
| rag-ingest | 8088 | `/ingest`, `/search`, `/query`, `/openapi.json` |
| postgres | 5432 | internal only by default |
| ollama | 11434 | host (Mac) or container (Linux) |
| oMLX | 8002 | host (Mac), key-protected; reached from containers as `host.docker.internal:8002` |

## Threat model, briefly

- Trust boundary is the Docker network. Only 3000 (and optionally 4000) should
  be exposed; put them behind your reverse proxy / tunnel with TLS.
- Virtual keys are the unit of access. Revoke a key, that user/app is out.
- Audit log answers "who asked what, when, against which model, at what cost."
- See `phi-pattern.md` for the regulated-data additions.
