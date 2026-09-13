# Voice demo — speak to a Mac, hear it answer

A public page whose inference is private. The browser records audio and talks only to this app; this
app holds the key for the inference endpoint and forwards to it. Nothing is written to disk, and the
demo has no path to any document corpus — it exists to show the speech loop, not retrieval.

```
browser ──► this app (VPS) ──► private endpoint (a Mac) ──► transcribe → answer → speak
```

You press start once and talk. The page listens continuously, notices when you stop, sends that turn,
speaks the answer and listens again — no push-to-talk.

**How the turn ends.** Voice activity detection runs in the browser on raw PCM: it measures the room's
noise floor for the first half second, treats anything 2.2× above it as speech, and ends the turn after
**850 ms** of quiet. Speech shorter than **350 ms** is ignored as a cough, a turn is cut at **30 s** so a
stuck gate cannot upload minutes of audio, and the **300 ms** before speech was detected is kept so the
first word survives. The microphone is gated off while the reply plays, so it never answers itself.

The browser sends 16 kHz mono WAV it encodes itself, so the server never has to decode a container
format. That removes the whole class of "every .webm upload 500s" failures, and 16 kHz is what every
backend resamples to anyway.

**Who has the floor** is shown as a rolling amplitude bar chart on a canvas: green while you speak,
teal while the Mac answers, flat grey when neither. Both sides feed the same history — your side from
the same RMS the voice detection already computes, the reply side from an `AnalyserNode` on the audio
element. No charting library; it is about sixty lines and it needs to read two sources a library would
not know about. One caveat worth knowing if you touch it: `createMediaElementSource` can be called only
once per element and reroutes that element's audio through the graph, so the AudioContext is created
once for the life of the page and deliberately not closed on stop.

**Conversation memory** is the last six turns, sent with each request. The server coerces every role to
user or assistant and truncates each turn, because the client is untrusted and an unbounded history is
a way to make someone else's GPU do free work.

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

## Models, and why each needs a different flag

The picker offers four chat models. Two of them will read their own scratchpad aloud unless told not
to, which is the single biggest cause of a reply that sounds strange:

| model | reply time | flag it needs | without the flag |
|---|---|---|---|
| Gemma 4 E4B | ~1 s | none | — |
| Qwen3 VL 8B | ~1.5 s | none | — |
| GPT-OSS 20B | ~2.5 s | `reasoning_effort: low` | thinking consumes the whole token budget; content comes back empty |
| Qwen3.8 27B | ~3-4 s | `chat_template_kwargs: {"enable_thinking": false}` | 10 s, and it says *"We need answer user's question:"* out loud |

Two related fixes live in the same place. The token budget is **220**, not 120 — three spoken
sentences overrun 120 often enough that answers were being cut mid-word, which reads as the model
being odd when it is really being truncated. And `reasoning_content` is never spoken: if a model
returns thinking but no content, the request fails cleanly instead of narrating deliberation.

`qwen3.8-27b-mtplx` is registered on the engine but 404s when called, so it is deliberately not offered.

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
