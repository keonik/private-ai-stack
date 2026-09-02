#!/usr/bin/env bash
# LoRA fine-tune with MLX-LM. Adjust --iters/--batch-size to the dataset; watch valid loss.
set -euo pipefail
BASE=${1:?base model (HF repo or local path)}
ITERS=${ITERS:-800}; BATCH=${BATCH:-4}; LR=${LR:-1e-5}; LAYERS=${LAYERS:-16}
python -m mlx_lm.lora --model "$BASE" --train --data data --adapter-path adapters \
  --iters "$ITERS" --batch-size "$BATCH" --learning-rate "$LR" --num-layers "$LAYERS" \
  --steps-per-eval 100 --save-every 200 --grad-checkpoint
echo "adapter saved to adapters/"
