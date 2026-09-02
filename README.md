# private-ai-stack

Reference builds for **self-hosted, private LLM systems** — the stack a small company runs when its data can't leave the building.

Everything here is public, runnable with one command, and documented like a runbook. Each subproject stands alone and is meant to be split into its own repo when it matures.

| Dir | What it is | Status |
|---|---|---|
| [`stack/`](stack/) | One-command private LLM platform: Ollama · LiteLLM · Open WebUI · RAG ingest · Postgres audit log · backups. Mac (Metal via host Ollama) and Linux (GPU container) profiles. | **runnable** |
| [`rag/`](rag/) | Private-docs RAG reference: hybrid retrieval (BM25 + vectors), reranking, page-level citations, and an eval harness that publishes real recall/faithfulness numbers. | scaffold |
| [`extract/`](extract/) | Structured extraction service: invoices and utility bills → schema-validated JSON with confidence scores and a human review queue. OCR via Recto. | scaffold |
| [`finetune/`](finetune/) | LoRA fine-tune → eval → GGUF pipeline on Apple Silicon (MLX-LM), delivering models that run in Ollama/llama.cpp. | scaffold |
| [`docs/`](docs/) | Architecture, the PHI/HIPAA-aware pattern, roadmap, write-ups. | — |

## Quick start

```bash
cd stack
cp .env.example .env         # set the three secrets
./scripts/pull-models.sh     # host Ollama pulls the default models (Mac)
docker compose up -d         # Mac: uses host Ollama for Metal
# Linux with NVIDIA: docker compose --profile linux up -d
open http://localhost:3000   # Open WebUI — first signup becomes admin
```

## Design principles

- **Every request is audited.** Open WebUI talks only to LiteLLM; LiteLLM logs every call to Postgres. Nothing bypasses the router.
- **Keys, not passwords, between services.** LiteLLM virtual keys per user/app with spend limits. Rotate without redeploying.
- **Runs on the hardware people actually have.** Mac Studio / Mac mini via Metal, or a Linux box with a GPU. Same compose file, two profiles.
- **Backups are a script, not a hope.** `scripts/backup.sh` dumps Postgres and every volume to a dated tarball; `restore.sh` reverses it.
- **PHI-aware by default.** Audit log, data minimization, access control, escalation rules — see [`docs/phi-pattern.md`](docs/phi-pattern.md).

## License

MIT — see [LICENSE](LICENSE).
