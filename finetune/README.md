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
