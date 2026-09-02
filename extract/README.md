# extract — documents in, validated JSON out, humans see only what needs them

Invoices and utility bills → Pydantic-validated JSON where **every field carries a
confidence and its evidence snippet**. Cross-field rules (subtotal + tax = total,
line items sum, date ordering) catch hallucinated numbers. Anything below the
confidence threshold or failing a rule lands in a review queue with a one-page
UI; approved records export to Excel or via API.

Scanned PDFs go through Recto (set `RECTO_URL`). Text PDFs are read directly.

## Run

```bash
uv venv && uv pip install -e .
export LITELLM_BASE_URL=http://localhost:4000/v1 LITELLM_API_KEY=sk-... CHAT_MODEL=local/chat
privextract                                   # http://localhost:8090  (review UI) · /docs (API)
curl -F file=@samples/inv.pdf localhost:8090/extract/invoice
curl localhost:8090/export -o extracted.xlsx
```

## Verified

`samples/sample-invoice.txt` through `local/chat` (`qwen3:8b`): 9/9 header fields
and 3/3 line items extracted, every field at confidence 1.0 with a verbatim
evidence snippet, all cross-field rules passed (subtotal + tax = total; line
items sum to subtotal), record landed in the queue as `ok`, xlsx export
produced a 12-column row. Try it:

```bash
curl -F file=@samples/sample-invoice.txt localhost:8090/extract/invoice | python3 -m json.tool
```

## Model choice: speed vs. certainty

Same synthetic invoice, same schema, through the router on an idle M4 Max.
Single runs, so treat the seconds as ±20%: oMLX prefix-caches prompts, and the
~6 KB schema+document prompt dominates, so a second call on the same document
type is noticeably faster than the first.

| `CHAT_MODEL` | backend · model | reasoning | wall | min field conf | result |
|---|---|---|---|---|---|
| `local/chat-small` | oMLX · gemma4-e4b | off | **12.8 s** | 0.90 | correct |
| `ollama/chat` | Ollama · qwen3:8b | off | 15.0 s | 0.99 | correct |
| `local/chat` | oMLX · Qwen3.8-27B | off | 45.1 s | 1.00 | correct |
| `local/chat` | oMLX · Qwen3.8-27B | on | 35.1 s | 1.00 | correct |

All four got every field and every line item right and passed the cross-field
rules; the difference is confidence and time. The production shape this
suggests: **run the small model on everything, and escalate only the flagged
documents** (low confidence or a failed rule) to the 27B. On a clean invoice
that is 13 s instead of 45; on a messy one you pay for the big model only when
the small one admits it isn't sure. `REVIEW_THRESHOLD` is the dial.

`EXTRACT_THINK=1` turns reasoning on. It did not change accuracy here and did
not reliably change speed; keep it off unless a document type proves otherwise.

## Add a document type

Add a Pydantic model to `schemas.py` using the `F[...]` field wrapper, register
it in `SCHEMAS`, add its cross-field rules to `validate()`. That's the whole
change — the API, queue, UI, and export are schema-agnostic.

## Pricing shape (for the productized version)

Per-document with a monthly floor. The review UI is what lets you promise
accuracy: the model does 95%, a human does the flagged 5%, the customer sees 100%.
