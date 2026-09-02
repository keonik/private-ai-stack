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
- [x] Synthetic corpus + 8-question set checked in (`rag/corpus`, `rag/eval/questions.jsonl`); multi-doc corpus in progress
- [x] Eval harness run end to end (recall@k, MRR, citation precision, answer-contains); numbers in `rag/README.md`
- [ ] Chunk-level ACL tags
- [ ] Compare 3 embedding models on the same eval

## 3. `extract/` — structured extraction product
- [x] Pydantic schemas (invoice, utility bill), LLM JSON-mode extraction, validation
- [x] Recto OCR hook (`RECTO_URL`) for scanned PDFs; text PDFs read directly
- [x] Review queue UI + approve endpoint; live-tested on a synthetic invoice (all fields conf 1.0, rules clean)
- [x] xlsx export verified · [ ] per-document pricing page

## 4. `finetune/` — LoRA → eval → GGUF
- [x] Scripts: prepare data, train LoRA (MLX-LM), eval, fuse, convert to GGUF
- [x] Worked example: synthetic invoice→JSON, Qwen3-4B-4bit LoRA — exact match 0.00 → 1.00, JSON valid 0.17 → 1.00, field match 0.38 → 1.00 (n=24, 11 min on M4 Max)
- [ ] Prove the GGUF runs in Ollama (`Modelfile`)

## 5. Write-ups
- [ ] "One-command private LLM stack on a Mac mini" (stack/)
- [ ] "Measuring RAG instead of vibing it" (rag/)
- [x] MLX Fast challenge: why local measurement fails on non-M5 silicon (see writeups/)
