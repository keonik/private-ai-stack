#!/usr/bin/env bash
# Fuse the adapter into the base (dequantized HF safetensors), export GGUF, write an Ollama Modelfile.
# Tries MLX-LM's native GGUF export first; falls back to llama.cpp's converter for architectures MLX can't export.
#   scripts/export_gguf.sh <base-model> [adapter-dir] [out-dir]
set -euo pipefail
BASE=${1:?base model}; ADAPTER=${2:-adapters}; OUT=${3:-fused}; GGUF=${GGUF_OUT:-fused.gguf}; TYPE=${GGUF_TYPE:-q8_0}
echo "fuse (dequantized) -> $OUT/"
mlx_lm.fuse --model "$BASE" --adapter-path "$ADAPTER" --save-path "$OUT" --dequantize
if mlx_lm.fuse --model "$BASE" --adapter-path "$ADAPTER" --save-path "$OUT" --dequantize --export-gguf --gguf-path "$GGUF" 2>/tmp/fuse-gguf.err && [ -s "$GGUF" ]; then
  echo "gguf via mlx-lm native export -> $GGUF"
else
  echo "mlx-lm native GGUF export unavailable for this architecture ($(tail -1 /tmp/fuse-gguf.err | cut -c1-90)); using llama.cpp converter"
  [ -d llama.cpp ] || git clone -q --depth 1 https://github.com/ggml-org/llama.cpp
  python llama.cpp/convert_hf_to_gguf.py "$OUT" --outfile "$GGUF" --outtype "$TYPE"
fi
cat > Modelfile <<MF
FROM ./$GGUF
PARAMETER temperature 0
SYSTEM You are a precise extraction assistant. Output only JSON.
MF
ls -la "$GGUF"; echo "ready:  ollama create <name> -f Modelfile"
