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

## Batch mode and sidecars

```
python scripts/batch.py --kind crash_oh1 --pages 1 --workers 4 /path/to/pdfs
python scripts/batch.py --kind crash_oh1 --pages 1 --only-flagged /path/to/pdfs   # escalation pass, bigger CHAT_MODEL
```

Writes `<file>.meta.json` next to each document: flattened field values, per-field confidence, evidence,
review flags, model, seconds. Existing sidecars are skipped, so re-runs only touch new files; `--only-flagged`
re-does the ones the first pass was unsure about. `stack/rag-ingest` reads the sidecars and turns the fields
into filters (`/fields`, `/documents`, filtered `/search`).

## Worked example: Ohio OH-1 crash reports (real public records, 501 files)

Schema `crash_oh1`: report number, date/time, county code, locality, agency, officer, and four narrative
judgments (animal, pedestrian/cyclist, alcohol/drugs, injury) plus a one-sentence summary.

Two things measured on this corpus that generalize to every form PDF:

1. **Coded boxes are not readable from flattened text, by any model.** Page 1 flattens to
   `1 NUMBER OF UNITS 98 - ANIMAL 99 - UNKNOWN 98 UNIT IN ERROR 5 1 - FATAL ...`. The 4B model read
   units=98, unit-in-error=5; the 27B read unit-in-error=5 (it is 98, the deer) and severity right by luck.
   Values sit next to legends and adjacency is a property of the PDF generator, not the form. Those fields
   need a layout-aware reader (word coordinates), so they are excluded from the LLM schema.
2. **Legends leak into judgments.** Given the full page, the 4B model marked 7 of 15 reports
   `animal_involved` (a ramp collision, a tool falling off a truck) because "98 - ANIMAL" is printed on every
   page. `privextract/prep.py` hands the model only the six caption-anchored header values plus the
   NARRATIVE block (about 400 characters instead of 4,400): 8/8 correct on the same files, 16/min instead
   of 12/min. If the anchors are missing (another agency's template) it falls back to the full page.

Results, 501 reports:

| pass | model | files | wall | rate | errors |
|---|---|---|---|---|---|
| extract everything | gemma4-e4b (4B) | 501 | 37 min | 13.5/min, 4 workers | 0 |
| verify every positive | Qwen3.8-27B | 113 | ~45 min | 2-3/min, 3 workers | 0 |

| field | 4B said true | after 27B verification | what the 4B got wrong |
|---|---|---|---|
| animal_involved | 31 | 30 | "struck an unknown object" |
| pedestrian_or_cyclist_involved | 15 | 12 | passenger stepping out of a truck; a car with no cyclist |
| alcohol_or_drugs_suspected | 13 | 1 | **12 of 13**: "cited for ACDA" (assured clear distance ahead) read as an alcohol citation, at confidence 1.0 |
| injury_mentioned | 59 | 39 | "no injuries reported" counted as a mention |

Recall check: a keyword scan of every narrative for alcohol/pedestrian terms found 4 reports the 4B had marked
false; the 27B agreed with the 4B on all 4 (e.g. "not intoxicated"). So on this corpus the small model's
misses are zero and its false positives are the whole problem, concentrated in one field, and its confidence
score does not know. The policy that follows: **small model on everything, big model on every positive.**
Positives are rare (about 20% of reports here), so the verification pass costs a fifth of a full 27B run.
Field names in the schema description matter: adding "ACDA, speed, licence and insurance citations are NOT
alcohol" is what let the 27B explain the distinction in its evidence on all 13.


## Cross-check against a rule-based parser (free ground truth)

The same 501 PDFs are processed by a separate, hand-written OH-1 parser (regexes over pdf.js text,
maintained in another repo for a different product). `scripts/gameplan_dump.ts` runs that parser over
the files and `scripts/compare_gameplan.py` scores the sidecars against it, field by field:

| field | agree | of | note |
|---|---|---|---|
| report_number | 501 | 501 | |
| crash date | 501 | 501 | |
| crash date + time | 501 | 501 | LLM writes Ohio wall clock; parser stores UTC — compared after conversion |
| county_code | 501 | 501 | |
| officer_name | 500 | 501 | the one miss is a dropped letter in a surname (LLM "Chiacchero", form "Chiacchiero") |

The parser reads coded boxes the LLM deliberately does not (see the coded-box limitation above), which
makes two booleans a comparison of *narrative* against *form*, not LLM against truth:

| | LLM true, form true | LLM true, form false | LLM false, form true |
|---|---|---|---|
| animal (narrative vs unit-in-error = 98 "deer") | 24 | 6 | 0 |
| injury (narrative vs any unit severity < 5) | 37 | 2 | 103 |

All six animal "extras" are correct readings of the narrative: a deer struck with the driver at fault, a
rear-end after the car ahead braked for an animal, three "swerved to avoid a deer" run-offs, and one
where the form coded the animal as unit 99. The 103 injury "misses" are the point made earlier from the
other side: the narrative rarely says "injured", the severity box does, and a chunk-and-prompt
extractor only sees the narrative. If a customer's question is "which crashes had injuries", the answer
must come from the form's coded fields, and this is the measurement that proves it. The two injury
extras are narratives that mention a transport to hospital where the officer coded no injury.

Reproduce (the parser repo path is an env var; it is not part of this repo):

```bash
ls ../stack/data/inbox/oh1-*.pdf | sed 's|.*/oh1-2026-08-03-|$REPORTS/|' \
  | xargs bun run scripts/gameplan_dump.ts > gameplan.jsonl      # 501 files, ~50 s
python scripts/compare_gameplan.py gameplan.jsonl ../stack/data/inbox
```

## Add a document type

Add a Pydantic model to `schemas.py` using the `F[...]` field wrapper, register
it in `SCHEMAS`, add its cross-field rules to `validate()`. That's the whole
change — the API, queue, UI, and export are schema-agnostic.

## Pricing shape (for the productized version)

Per-document with a monthly floor. The review UI is what lets you promise
accuracy: the model does 95%, a human does the flagged 5%, the customer sees 100%.
