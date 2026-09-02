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

## Add a document type

Add a Pydantic model to `schemas.py` using the `F[...]` field wrapper, register
it in `SCHEMAS`, add its cross-field rules to `validate()`. That's the whole
change — the API, queue, UI, and export are schema-agnostic.

## Pricing shape (for the productized version)

Per-document with a monthly floor. The review UI is what lets you promise
accuracy: the model does 95%, a human does the flagged 5%, the customer sees 100%.
