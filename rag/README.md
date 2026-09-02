# rag — private-docs RAG reference build, with numbers

Hybrid retrieval (BM25 + vectors, reciprocal rank fusion), optional reranking,
page-level citations, chunk-level ACLs, and an **eval harness** that reports
recall@k, MRR, citation precision, and answer correctness on a checked-in
question set.

The deployable lite version of this lives in `../stack/rag-ingest`. This is the
version you measure and iterate on.

## Corpus

`corpus/` holds five synthetic documents (MSA, SLA, NDA, privacy policy, SOW) that share vendor names and vocabulary so retrieval has real distractors. Never put customer data here. Add your own public docs and extend `eval/questions.jsonl` with hand-verified answers.

## Run

```bash
uv venv && uv pip install -e ".[eval]"
export LITELLM_BASE_URL=http://localhost:4000/v1 LITELLM_API_KEY=sk-...   # from ../stack
privrag ingest corpus/
privrag search "termination notice period"
privrag ask "What is the notice period?" --rerank
privrag eval --k 6                 # writes eval/results.json
privrag eval --k 6 --rerank        # compare
```

## Results

Measured with `privrag eval --k 4`, `local/chat` = `qwen3:8b` via Ollama, `local/embed` = `nomic-embed-text`, on an M4 Max.

**Smoke corpus** — one synthetic contract (`corpus/sample-msa.txt`), 8 hand-verified questions. With a single document retrieval cannot fail, so this only proves the pipeline is wired correctly; it is not a benchmark.

| config | n | recall@4 | MRR | citation precision | answer ok |
|---|---|---|---|---|---|
| hybrid, no rerank | 8 | 1.00 | 1.00 | 1.00 | 1.00 |
| hybrid + LLM rerank | 8 | 1.00 | 1.00 | 1.00 | 1.00 |

**Multi-document corpus** — five synthetic documents (MSA, SLA, NDA, privacy policy, SOW) that share vendor names, party names, and vocabulary, 15 chunks of ~120 tokens, 24 hand-verified questions. Retrieval can and does mis-rank here. Same harness, two backends behind the same router:

| backend (chat · embed) | rerank | recall@4 | MRR | top-1 correct | citation precision@4 | answer ok |
|---|---|---|---|---|---|---|
| Ollama · qwen3:8b · nomic-embed-text | no | 1.00 | 0.948 | 22/24 | 0.385 | 24/24 |
| Ollama · qwen3:8b · nomic-embed-text | **yes** | 1.00 | **0.979** | **23/24** | 0.365 | 24/24 |
| oMLX · Qwen3.8-27B-4bit · embeddinggemma-300m | no | 1.00 | 0.938 | 21/24 | 0.389 | 24/24 |
| oMLX · Qwen3.8-27B-4bit · embeddinggemma-300m | **yes** | 1.00 | **0.972** | **23/24** | 0.375 | 24/24 |

Two things fall out of the second pair. Swapping the backend — different engine, different chat model, different embedding model — moved nothing that matters, which is what a router is for. And the reranker's lift is consistent: +1 to +2 questions into first place on both backends. Wall clock on the oMLX rows: ~3.8 min without rerank, ~6.9 min with, because the 27B runs at ~28 tok/s and reranking adds 96 scoring calls.

How to read it:

- **Recall@4 = 1.0** means the right document was always somewhere in the top 4. With 15 chunks that is a low bar; it will drop on a real corpus.
- **MRR and top-1** are where reranking earns its keep: one more question got the right chunk in first place. That is the number that matters when the answer prompt only trusts source [1].
- **Citation precision@4 is low by construction.** Every question has one correct source and we return four chunks, so the ceiling is 0.25 for single-source questions and 0.5 for the two dual-source ones. 0.38 means the extra chunks are mostly from the right document's neighbours. If you want this number high, return fewer chunks or measure precision@1 — which is the top-1 column.
- **Answer ok 24/24** after fixing one over-strict label ("no subprocessors" is a correct answer to "which subprocessors"; the check wanted "none"). Before the fix the model was right and the label was wrong. Store the model's answer in the results file so you can tell those cases apart; `evaluate.py` does now.
- **The reranker silently did nothing** in the first run: `/no_think` in the prompt is not honored via LiteLLM → Ollama, so the 16-token budget was spent thinking and no score came back. Every passage tied at -1, order unchanged, numbers identical to no-rerank. Sending `think: false` in the request fixed it. A reranker that returns identical numbers to no-rerank is a bug until proven otherwise.

Wall clock on an M4 Max with `qwen3:8b`: ~80 s for the 24-question eval without rerank, ~170 s with (96 extra scoring calls).

## Design notes

- **Hybrid because legal/financial queries hinge on exact tokens** (clause
  numbers, invoice ids) that embeddings blur. RRF is parameter-free and robust.
- **Citations are page-level** so a human can verify in seconds.
- **ACL at chunk level** — tag at ingest, filter at query. Required for
  multi-user deployments over sensitive documents.
- **Reranker is pluggable**: default pointwise LLM scoring works with any
  backend; set `RERANK_MODEL` to use a cross-encoder endpoint.
- **Answer-only-from-sources** prompt with an explicit not-found string, so
  "no answer" is a measurable outcome instead of a hallucination.
