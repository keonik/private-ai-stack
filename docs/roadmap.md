# Roadmap

Ordered by what buyers ask for most, weighted by distance from what exists.

## 1. `stack/` — private LLM platform (runnable now)
- [x] Compose: Postgres, LiteLLM, Open WebUI, rag-ingest; Mac + Linux profiles
- [x] Virtual keys, audit to Postgres, backup/restore scripts
- [ ] `docker compose --profile llamacpp` alternative backend
- [x] Reverse-proxy example (Caddy) with TLS (`stack/Caddyfile.example`)
- [ ] Prometheus/Grafana or Langfuse for observability
- [x] Smoke test: `stack/scripts/smoke.sh` (health → models → embed → chat → ingest → search → cited answer → audit → spend log); CI workflow on a CPU runner with qwen3:0.6b

## 2. `rag/` — reference build with eval
- [x] Hybrid retrieval (BM25 + vector, RRF), reranker hook, page citations
- [ ] Public corpus + question set checked in
- [ ] RAGAS-style eval run; publish numbers in README
- [ ] Chunk-level ACL tags
- [ ] Compare 3 embedding models on the same eval

## 3. `extract/` — structured extraction product
- [x] Pydantic schemas (invoice, utility bill), LLM JSON-mode extraction, validation
- [ ] Recto OCR integration for scans
- [ ] Review queue UI for low-confidence fields
- [ ] Excel/CSV export; per-document pricing page

## 4. `finetune/` — LoRA → eval → GGUF
- [x] Scripts: prepare data, train LoRA (MLX-LM), eval, fuse, convert to GGUF
- [ ] One worked example with before/after numbers
- [ ] Prove the GGUF runs in Ollama (`Modelfile`)

## 5. Write-ups
- [ ] "One-command private LLM stack on a Mac mini" (stack/)
- [ ] "Measuring RAG instead of vibing it" (rag/)
- [x] MLX Fast challenge: why local measurement fails on non-M5 silicon (see writeups/)
