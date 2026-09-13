#!/usr/bin/env bash
# Re-test which kokoro voices synthesise on the engine, e.g. after changing its packages.
#   OMLX_URL=http://127.0.0.1:8002/v1 OMLX_API_KEY=sk-... ./check_voices.sh
# Prints one "<voice> <http-code>" per line; 200 means usable. Voices come from the model directory,
# so run it on the machine holding ~/omlx-models/kokoro-tts.
set -uo pipefail
K=$(readlink -f "${KOKORO_DIR:-$HOME/omlx-models/kokoro-tts}")
for f in "$K"/voices/*.pt; do
  v=$(basename "$f" .pt)
  code=$(curl -s -o /dev/null -m 60 -w "%{http_code}" -H "Authorization: Bearer $OMLX_API_KEY" \
    -H 'content-type: application/json' "$OMLX_URL/audio/speech" \
    -d "{\"model\":\"${TTS_MODEL:-kokoro-tts}\",\"input\":\"Hello, this is a test of the voice.\",\"voice\":\"$v\",\"response_format\":\"wav\"}")
  echo "$v $code"
done
