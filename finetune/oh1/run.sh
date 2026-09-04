#!/usr/bin/env bash
# before-eval → LoRA → after-eval on the OH-1 set. Logs to oh1/run.log.
set -euo pipefail; cd "$(dirname "$0")/.."; source .venv/bin/activate
BASE=mlx-community/Qwen3-4B-4bit
echo "== before $(date -u +%T)"; python scripts/eval.py --model $BASE --split oh1/data/test.jsonl --out oh1/eval/before.json --max-tokens 400
echo "== train $(date -u +%T)"; ITERS=300 BATCH=4 LR=2e-5 LAYERS=16 DATA=oh1/data ADAPTERS=oh1/adapters scripts/train_lora.sh $BASE
echo "== after $(date -u +%T)"; python scripts/eval.py --model $BASE --adapter oh1/adapters --split oh1/data/test.jsonl --out oh1/eval/after.json --max-tokens 400
echo "== done $(date -u +%T)"
