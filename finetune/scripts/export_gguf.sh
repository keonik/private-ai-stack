#!/usr/bin/env bash
# Fuse adapter into the base (HF safetensors), then convert to GGUF with llama.cpp's converter, then write an Ollama Modelfile.
set -euo pipefail
BASE=${1:?base model}; ADAPTER=${2:-adapters}; OUT=${3:-fused}
python -m mlx_lm.fuse --model "$BASE" --adapter-path "$ADAPTER" --save-path "$OUT" --de-quantize
if [ ! -d llama.cpp ]; then git clone --depth 1 https://github.com/ggml-org/llama.cpp; fi
python llama.cpp/convert_hf_to_gguf.py "$OUT" --outfile fused.gguf --outtype ${GGUF_TYPE:-q8_0}
cat > Modelfile <<MF
FROM ./fused.gguf
PARAMETER temperature 0
SYSTEM You are a precise extraction assistant. Output only JSON.
MF
echo "fused.gguf + Modelfile ready →  ollama create my-model -f Modelfile"
