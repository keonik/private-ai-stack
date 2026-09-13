# Voice demo — speak to a Mac, hear it answer

A public page whose inference is private. The browser records audio and talks only to this app; this
app holds the key for the inference endpoint and forwards to it. Nothing is written to disk, and the
demo has no path to any document corpus — it exists to show the speech loop, not retrieval.

```
browser ──► this app (VPS) ──► private endpoint (a Mac) ──► transcribe → answer → speak
```

The browser re-encodes its recording to 16 kHz mono WAV before uploading, so the server never has to
decode a container format. That removes the whole class of "every .webm upload 500s" failures, and
16 kHz is what every backend resamples to anyway.

## Run it

```bash
docker build -t voice-demo . && docker run -p 8080:8080 \
  -e INFER_BASE_URL=https://your-endpoint/v1 \
  -e INFER_API_KEY=sk-... \
  voice-demo
```

| variable | default | what it does |
|---|---|---|
| `INFER_BASE_URL` | — | OpenAI-compatible endpoint, required |
| `INFER_API_KEY` | — | its key, required; never reaches the browser |
| `STT_MODEL` | `parakeet-tdt-0.6b-v2` | transcription |
| `CHAT_MODEL` | `gemma4-e4b-mlx` | the answer |
| `TTS_MODEL` | `kokoro-tts` | speech |
| `DAILY_BUDGET` | `2000` | requests per UTC day before the demo closes itself |
| `DEMO_ENABLED` | `1` | set to `0` to take it down without redeploying |

## Voices

Kokoro ships 54 voice files. **41 synthesise on this engine; all 13 Japanese and Chinese ones return
500**, because they need misaki's `ja` / `zh` phonemizers, which the engine's environment does not
carry. The page offers only the working 41, grouped by language:

| language | voices |
|---|---|
| American English | 20 |
| British English | 8 |
| Hindi | 4 |
| Spanish, Brazilian Portuguese | 3 each |
| Italian | 2 |
| French | 1 |

The list is checked into `app.py` rather than discovered at runtime, because this app runs on a
different machine from the model directory. An unknown voice is refused here with a 400 rather than
forwarded, since the engine answers one with a 500 quoting a filesystem path. Re-run `check_voices.sh`
on the machine holding the models if the engine's packages change.

## Guards, because this points at someone's GPU

Twenty requests per five minutes per address, a four megabyte upload ceiling (about two minutes of
audio), four hundred characters of text into the chat and speech steps, a daily budget, and a kill
switch. Give it its own API key on the endpoint so it can be rate-limited and revoked without touching
anything else.

## Why it keeps itself warm

Models are pooled and evicted, so the first visitor after a quiet spell pays the load. Measured on the
reference machine, five seconds of audio:

| | cold | warm |
|---|---|---|
| synthesise | 3.65 s | **1.19 s** |
| transcribe | 4.09 s | **0.49 s** (10× realtime) |
| answer | — | **0.45 s** |

A round trip every four minutes keeps all three resident. The warm-up synthesises one word and feeds
that audio straight into transcription, so it warms two models for the price of one and needs no
fixture file.
