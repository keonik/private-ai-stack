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

**Multi-document corpus** — see the next table once it lands; that one has distractor documents with overlapping vocabulary, so recall and citation precision can actually drop.

The honest way to read any RAG number: if recall@k is 1.0, your corpus is too small or your questions are too easy. Add documents until it isn't, then optimize.

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
