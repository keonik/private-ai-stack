# rag — private-docs RAG reference build, with numbers

Hybrid retrieval (BM25 + vectors, reciprocal rank fusion), optional reranking,
page-level citations, chunk-level ACLs, and an **eval harness** that reports
recall@k, MRR, citation precision, and answer correctness on a checked-in
question set.

The deployable lite version of this lives in `../stack/rag-ingest`. This is the
version you measure and iterate on.

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

| config | recall@6 | MRR | citation precision | answer ok |
|---|---|---|---|---|
| hybrid, no rerank | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| hybrid + LLM rerank | _tbd_ | _tbd_ | _tbd_ | _tbd_ |

Fill this in from a public corpus before showing it to anyone. Numbers you
measured beat adjectives you wrote.

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
