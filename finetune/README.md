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

## Worked example (fill in)

| | exact match | JSON valid | notes |
|---|---|---|---|
| base | _tbd_ | _tbd_ | |
| + LoRA | _tbd_ | _tbd_ | |

Suggested first task: teach a 4B model to emit the `extract/` invoice schema
from raw text. The eval is exact-match on fields, which is unambiguous, and the
result plugs straight into the extraction product.

## What the machine does overnight

Training on 128 GB unified memory: 7B–14B bf16 LoRA fits comfortably; 30B in
4-bit fits for QLoRA-style runs. Kick it off before bed.
