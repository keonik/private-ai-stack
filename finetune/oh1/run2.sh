#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")/.."; source .venv/bin/activate
BASE=mlx-community/Qwen3-4B-4bit
echo "== before-schema $(date -u +%T)"; python scripts/eval.py --model $BASE --split oh1/data/test-schema.jsonl --out oh1/eval/before-schema.json --max-tokens 400
echo "== after $(date -u +%T)"; python scripts/eval.py --model $BASE --adapter oh1/adapters --split oh1/data/test.jsonl --out oh1/eval/after.json --max-tokens 400
echo "== done $(date -u +%T)"
