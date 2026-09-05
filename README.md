# private-ai-stack

Reference builds for **self-hosted, private LLM systems** — the stack a small company runs when its data can't leave the building.

Everything here is public, runnable with one command, and documented like a runbook. Each subproject stands alone and is meant to be split into its own repo when it matures.

| Dir | What it is | Status |
|---|---|---|
| [`stack/`](stack/) | One-command private LLM platform: a local engine (oMLX on Apple Silicon, Ollama on Linux) · LiteLLM router with per-key budgets and a spend log · Open WebUI · rag-ingest · Postgres audit log · backups · an optional observability profile (Prometheus, Loki, Grafana, phone alerts). | **verified** — smoke test end to end; backup/restore round-trip proven; alerting tested by breaking it on purpose |
| [`rag/`](rag/) | Private-docs RAG reference: hybrid retrieval (BM25 + vectors), reranking, page-level citations, group-scoped access, and an eval harness that publishes real recall/MRR/citation numbers. | **verified** — measured on 501 real forms as well as a synthetic corpus; keyword weighting raised recall@5 from 0.77 to 0.82, confirmed on a held-out question set |
| [`extract/`](extract/) | Structured extraction service: forms, invoices and utility bills → schema-validated JSON with per-field confidence + evidence, cross-field rules, review queue, xlsx export. Two-tier: a 4B model reads everything, a 27B model re-checks the answers that matter. | **verified** — 501 real crash reports; on a publishable synthetic set, 640/640 field values, and 22 legend-leakage false positives removed by preprocessing |
| [`finetune/`](finetune/) | LoRA fine-tune → eval → GGUF pipeline on Apple Silicon (MLX-LM), delivering models that run in Ollama/llama.cpp. | **verified** — Qwen3-4B on real forms: 0.87 → 0.98 on structured fields from a prompt a twentieth the size, 15 min of training |
| [`docs/`](docs/) | Architecture, the PHI/HIPAA-aware pattern, roadmap, write-ups. | — |

## Quick start

```bash
cd stack
cp .env.example .env         # set the secrets; point OMLX_BASE_URL at your engine
docker compose up -d         # Mac: talks to a local engine on the host for Metal
# Linux with NVIDIA:  ./scripts/pull-models.sh && docker compose --profile linux up -d
# with dashboards:    docker compose --profile obs up -d   → Grafana on :3001
open http://localhost:3000   # Open WebUI — first signup becomes admin
```

## Design principles

- **Every request is audited.** Open WebUI talks only to LiteLLM; LiteLLM logs every call to Postgres. Nothing bypasses the router.
- **Keys, not passwords, between services.** LiteLLM virtual keys per user/app with spend limits. Rotate without redeploying.
- **Runs on the hardware people actually have.** Mac Studio / Mac mini via Metal, or a Linux box with a GPU. Same compose file, two profiles.
- **Measured, not asserted.** Every "verified" above is a number produced by a script in this repo, including the ones that came out badly. Where a method has a limit — coded checkboxes a language model cannot read, synthetic data being easier than scans — it is written down next to the result.
- **Backups are a script, not a hope.** `scripts/backup.sh` dumps Postgres and every volume to a dated tarball; `restore.sh` reverses it.
- **PHI-aware by default.** Audit log, data minimization, access control, escalation rules — see [`docs/phi-pattern.md`](docs/phi-pattern.md).

## License

MIT — see [LICENSE](LICENSE).
