# finetune — LoRA → eval → GGUF on Apple Silicon

Train a LoRA adapter with MLX-LM on the Mac, evaluate before/after on a held-out
set, fuse, convert to GGUF, and prove it runs in Ollama. Deliverables are in the
formats buyers actually use (Hugging Face safetensors + GGUF), not MLX-only.

## Pipeline

```bash
uv venv && uv pip install mlx-lm datasets huggingface_hub
python scripts/prepare_data.py raw/ data/            # → data/{train,valid,test}.jsonl (chat format)
python scripts/eval.py --model $BASE --split data/test.jsonl --out eval/before.json
scripts/train_lora.sh $BASE                          # → adapters/
python scripts/eval.py --model $BASE --adapter adapters --split data/test.jsonl --out eval/after.json
scripts/export_gguf.sh $BASE adapters                 # → fused/ (HF) and fused.gguf
ollama create my-model -f Modelfile && ollama run my-model
```

`BASE` is any MLX-compatible HF repo, e.g. `mlx-community/Qwen3-4B-4bit` for
iteration or the bf16 repo for a fuse you intend to quantize downstream.

## Worked example: invoice → JSON

Task: emit a flat JSON record (vendor, invoice number, dates, currency, subtotal,
tax, total) from raw invoice text. Data: 240 synthetic invoices across three
layouts from `scripts/gen_synthetic_invoices.py` (deterministic, exact labels,
no LLM in the loop), split 192 / 24 / 24. Base: `mlx-community/Qwen3-4B-4bit`.
LoRA on 16 layers, 300 iterations, batch 4, lr 2e-5, on an M4 Max.

| | exact match | JSON valid | field match |
|---|---|---|---|
| base (thinking off) | **0.00** | 0.17 | 0.38 |
| + LoRA, 300 iters | **1.00** | 1.00 | 1.00 |

n = 24 held-out invoices. Wall clock: 47 s before-eval, **10 min 54 s training**, 29 s after-eval.

Loss trajectory (val at iter 1 / 100 / 200 / 300): 2.80 → 0.21 → 0.21 → 0.26.
Train loss kept falling (0.56 → 0.14) while val ticked up after 200, so ~200
iterations is the honest stopping point for this dataset; 300 is mildly
over-fit and still generalizes to the held-out split. Adapter: 29 MB.

Caveats, stated plainly: the data is synthetic and the layouts are regular, so
this shows the pipeline and the *shape* of the gain, not production accuracy on
messy scans. The next step is the same run on a few hundred real, de-identified
invoices — which is exactly the engagement this demonstrates you can deliver.

Reproduce:

```bash
python scripts/gen_synthetic_invoices.py 240 && python scripts/prepare_data.py raw data
BASE=mlx-community/Qwen3-4B-4bit
python scripts/eval.py --model $BASE --split data/test.jsonl --out eval/before.json --max-tokens 200
ITERS=300 BATCH=4 LR=2e-5 LAYERS=16 scripts/train_lora.sh $BASE
python scripts/eval.py --model $BASE --adapter adapters --split data/test.jsonl --out eval/after.json --max-tokens 200
```

## Worked example 2: real forms, labels from a bigger model (OH-1 crash reports)

The engagement shape the invoice example points at, run for real: 501 Ohio OH-1 crash reports (public
records, `../extract`), labelled by the two-tier extractor — a 4B model extracts everything, a 27B model
re-checks every positive — and cross-checked against a rule-based parser (report number, date, time,
county 501/501). Input is the same reduced page-1 text the extractor saw (header captions + narrative,
median 480 characters); output is the 11 field values as compact JSON. Split 400 / 50 / 51, seed 0.
Base `mlx-community/Qwen3-4B-4bit`, LoRA on 16 layers, 300 iterations, batch 4, lr 2e-5.

`scripts/prepare_oh1.py` builds the set from the sidecars; `oh1/run.sh` and `oh1/run2.sh` are the exact
runs below.

| n = 51 held-out reports | JSON valid | field match, all 11 | field match, 10 structured fields | county_code | agency | officer | injury |
|---|---|---|---|---|---|---|---|
| base, 60-character system prompt (no schema) | 1.00 | 0.09 | — | invents its own keys | | | |
| base, schema in the prompt (1,200 characters) | 1.00 | 0.80 | 0.87 | **0.00** | 0.92 | 0.90 | 0.98 |
| base + LoRA, 60-character prompt | 1.00 | 0.90 | **0.98** | 1.00 | 0.96 | 0.92 | 1.00 |

Wall clock on the M4 Max while the chat stack kept serving: before-evals ~2 min each, **training
15 min** (val loss 3.01 → 0.72 at iteration 100, flat after; 200 is the honest stopping point again),
after-eval 2.5 min. Adapter 29 MB.

Read it carefully, because the middle row is the one a buyer should ask about. Given the schema in the
prompt, the base model already understands the task; what it gets wrong is *convention* — `"18"` where
the label is `18`, `SOLON POLICE DEPARTMENT` where the label is `Solon Police Department`, an officer
name with the rank left in. The adapter learns the house style from 400 examples and takes the ten
structured fields to 0.98 with a prompt one twentieth the size and no JSON-schema constrained decoding.
The eleventh field, `summary`, is free text and never matches exactly (0.02 → 0.10); exact match is the
wrong metric for it and it is excluded from the third column on purpose.

So the honest claim is not "fine-tuning made the model smart"; it is "fine-tuning made the small model
produce exactly the record the downstream system expects, first time, from a short prompt", which is
what removes the verification tier for the routine fields. The remaining misses are the same three
spelling-level cases across all rows (an agency abbreviated on the form, a surname the model
regularises), which is also where the 27B verifier stays.

## GGUF export, verified in Ollama

`scripts/export_gguf.sh` fuses the adapter into a dequantized copy of the base,
tries MLX-LM's native GGUF export, and falls back to llama.cpp's converter
(needed for Qwen3). Output here: `fused.gguf`, **4.28 GB at q8_0**, loaded with
`ollama create invoice-extractor -f Modelfile`, then queried through Ollama's
API with `think: false`:

| held-out invoice | Ollama (GGUF q8_0) vs label |
|---|---|
| Tailspin Toys #34233 | exact match |
| Litware Consulting #46482 | exact match |
| Fabrikam Services IN84955 (EUR) | exact match |

So the model a customer receives is the model that was evaluated, in the runtime
they already have. Gotchas hit on the way: the fuse flag is `--dequantize`
(no hyphen); Hugging Face's cache check refuses an "incomplete snapshot" if two
metadata files were never fetched — one `snapshot_download()` with network
fixes it; llama.cpp's converter needs `torch`, `transformers`, `gguf`,
`sentencepiece`.

## What the machine does overnight

Training on 128 GB unified memory: 7B–14B bf16 LoRA fits comfortably; 30B in
4-bit fits for QLoRA-style runs. Kick it off before bed.
