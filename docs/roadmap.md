# Roadmap

Ordered by what buyers ask for most, weighted by distance from what exists.

## 1. `stack/` — private LLM platform (runnable now)
- [x] Compose: Postgres, LiteLLM, Open WebUI, rag-ingest; Mac + Linux profiles
- [x] Virtual keys, audit to Postgres, backup/restore scripts
- [x] Second backend wired and swapped live: oMLX (chat/embed/STT) alongside Ollama; router-only change
- [ ] `docker compose --profile llamacpp` alternative backend
- [x] Reverse-proxy example (Caddy) with TLS (`stack/Caddyfile.example`)
- [x] Observability profile: Prometheus + blackbox + docker-stats exporter + Loki/Alloy + Grafana, two provisioned dashboards (overview incl. TTFT and spend from Postgres; RAG service), six alert rules to a webhook; alert delivery verified end to end
- [x] Deployed on the reference Mac: admin claimed, signup off, rag-ingest registered as an admin-level tool server, exposed through a Cloudflare tunnel (hostname kept out of the repo)
- [x] Extracted fields as filters: `/fields`, `/documents`, filtered `/search` and `/query` from sidecars
- [x] Scale check: 501 form PDFs → 9,201 chunks in 5 min; batch BM25 rebuild + compaction, content-hash skip, polling watcher (macOS bind-mount inotify gap), filename identifier lookup; field-level questions over forms documented as out of scope for chunk RAG
- [x] Deletions: file removed from the inbox → chunks removed (watcher, `/ingest` reconcile, startup sweep); re-saved file replaces its chunks
- [x] Nightly backup + 5-min compose keepalive as launchd agents (`install-launchd.sh`)
- [x] Retrieval eval on the 501 real forms (`rag_eval.py`, synthetic questions from sidecars, two seeds): BM25 ≫ vector on homogeneous forms; lexical weight 3 in the fusion, recall@5 0.77 → 0.82
- [x] Smoke test: `stack/scripts/smoke.sh` (health → models → embed → chat → ingest → search → cited answer → audit → spend log); CI workflow on a CPU runner with qwen3:0.6b

## 2. `rag/` — reference build with eval
- [x] Hybrid retrieval (BM25 + vector, RRF), reranker hook, page citations
- [x] Synthetic 5-doc corpus with distractors + 24-question set checked in (`rag/corpus`, `rag/eval/questions.jsonl`)
- [x] Eval harness run end to end (recall@k, MRR, citation precision, answer-contains); numbers in `rag/README.md`
- [x] Document-level ACL by Open WebUI group (`data/acl.json` + `{{USER_GROUPS}}` header; filters and ACL compose); chunk-level tags not needed for this shape
- [x] Same eval on two backends (Ollama/nomic vs oMLX/embeddinggemma): quality unchanged, rerank lift consistent
- [ ] Compare 3 embedding models on the same eval
- [x] Rerank vs no-rerank compared on the 5-doc corpus: MRR 0.948 → 0.979, top-1 22 → 23 of 24

## 3. `extract/` — structured extraction product
- [x] Pydantic schemas (invoice, utility bill), LLM JSON-mode extraction, validation
- [x] Recto OCR hook (`RECTO_URL`) for scanned PDFs; text PDFs read directly
- [x] Review queue UI + approve endpoint; live-tested on a synthetic invoice (all fields conf 1.0, rules clean)
- [x] Two-tier extraction measured on 501 real forms: 4B everything (37 min), 27B verifies positives; alcohol false positives 12/13 → 0
- [x] Cross-checked against a rule-based OH-1 parser on all 501: report number/date/time/county 501/501, officer 500/501; narrative-vs-coded-box gap measured (103 coded injuries the narrative never mentions)
- [x] Batch mode → metadata sidecars; OH-1 schema + caption-anchored preprocessor; coded-box limitation measured and documented
- [x] xlsx export verified · [x] model tiering measured (small model 13 s/doc, escalate flagged docs to 27B) · [ ] per-document pricing page

## 4. `finetune/` — LoRA → eval → GGUF
- [x] Scripts: prepare data, train LoRA (MLX-LM), eval, fuse, convert to GGUF
- [x] Worked example: synthetic invoice→JSON, Qwen3-4B-4bit LoRA — exact match 0.00 → 1.00, JSON valid 0.17 → 1.00, field match 0.38 → 1.00 (n=24, 11 min on M4 Max)
- [x] Real-data run: Qwen3-4B LoRA on 501 OH-1 reports labelled by the two-tier extractor — 10 structured fields 0.87 → 0.98 vs the schema-prompted base, from a 60-character prompt; 15 min training
- [x] GGUF q8_0 (4.28 GB) exported via llama.cpp converter, loaded in Ollama, 3/3 exact on held-out invoices

## 5. Write-ups
- [ ] "One-command private LLM stack on a Mac mini" (stack/)
- [ ] "Measuring RAG instead of vibing it" (rag/)
- [x] MLX Fast challenge: why local measurement fails on non-M5 silicon (see writeups/)
