# Voice demo — speak to a Mac, hear it answer

A public page whose inference is private. The browser records audio and talks only to this app; this
app holds the key for the inference endpoint and forwards to it. Nothing is written to disk, and the
demo has no path to any document corpus — it exists to show the speech loop, not retrieval.

```
browser ──► this app (VPS) ──► private endpoint (a Mac) ──► transcribe → answer → speak
```

You press start once and talk. The page listens continuously, notices when you stop, sends that turn,
speaks the answer and listens again — no push-to-talk.

**How the turn ends.** Voice activity detection runs in the browser on raw PCM.

The noise floor is the **20th percentile of the last 1.8 seconds** — what the room sounds like when
nobody is talking. Two earlier attempts were wrong in ways worth recording. Measuring the floor once at
start-up meant a throat-clear during that second poisoned it for the whole session. Replacing that with
a running average was worse: it converges too slowly to be right in the first second, and speech drags
it upward, raising the bar exactly when someone is trying to clear it. A percentile ignores the loud
tail entirely and settles in about a second.

Two thresholds, not one: opening a turn takes a clear signal, continuing it takes much less, because
the gaps between words are quieter than the words. **Speech time accumulates and decays rather than
resetting** — the first version zeroed it on any quiet block, so only a continuous shout could ever
open the gate, which is exactly how it failed in use.

A turn ends after **700 ms** of quiet, anything under **200 ms** is a cough, a turn is cut at **30 s**,
and the **400 ms** before speech was detected is kept so the first word survives. The microphone is
gated off while the reply plays, so it never answers itself. The visualiser draws the opening threshold
as a pair of faint lines, so it is visible when a voice simply is not clearing it.

**Sensitivity** is a control on the page, because only the person in the room knows whether there is a
fan running. `vad_sim.js` replays the gate offline against synthetic speech at several volumes — the
gate cannot be tested from a terminal, and every bug above was found by replaying it rather than by
reading the code.

The browser sends 16 kHz mono WAV it encodes itself, so the server never has to decode a container
format. That removes the whole class of "every .webm upload 500s" failures, and 16 kHz is what every
backend resamples to anyway.

**Who has the floor** is shown as a rolling amplitude bar chart on a canvas: green while you speak,
teal while the Mac answers, flat grey when neither. Your side comes from the RMS the voice detection
already computes. The reply's side is **computed from the WAV bytes**, not from the audio graph.

That last choice is load-bearing on phones. The first version routed the reply through a
`MediaElementSource` so an `AnalyserNode` could watch it, and on mobile that made the reply **silent
while transcription kept working** — because on iOS the WebAudio path obeys the hardware silent switch
and outputs nothing from a suspended or interrupted context, while a plain media element does neither.
Playback is now ordinary `<audio>`, the envelope is parsed from the PCM, and the bars are indexed by
`currentTime`. Same picture, no audio graph.

Two more mobile rules are handled: the element is marked `playsinline`, and a fraction of a second of
silence is played inside the tap that starts the conversation, which is what marks the element as
user-permitted so later replies can play themselves. If a browser still refuses, the page says so
instead of appearing to work.

`env_test.js` checks the parser against a real reply from the engine.

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

Six models in the picker, ranked by how good the answer is per second of waiting. Median of four
voice-shaped questions — a capability question, one that should be refused, one testing recall of the
previous turn, and one asking for a plain explanation — measured 2026-09-13:

| model | median | flag it needs | without the flag |
|---|---|---|---|
| **Qwen3.5 9B** (default) | **0.87 s** | `enable_thinking: false` | reads *"Thinking Process: 1. Analyze the Request"* aloud |
| Qwen3 VL 4B | 0.51 s | none | — |
| Qwen3.5 4B | 0.59 s | `enable_thinking: false` | same leak as the 9B |
| Gemma 4 E4B | 0.61 s | none | — |
| GPT-OSS 20B | ~2.5 s | `reasoning_effort: low` | thinking eats the budget; content comes back empty |
| Qwen3.8 27B | ~3 s | `enable_thinking: false` | 10 s, and it says *"We need answer user's question:"* |

**Every Qwen generation ships thinking on and writes it into `content`, not `reasoning_content`.** That
one fact explains most of what looks like a model being strange out loud. Two other fixes live here:
the budget is **220 tokens**, not 120, because three spoken sentences were being cut mid-word; and
`reasoning_content` is never spoken — if a model thinks but returns no content, the request fails
cleanly rather than narrating deliberation.

**Measured and rejected.** Phi-4-mini is quick (0.55 s) and was the only model to get a plain recall
question wrong, answering what it was rather than what it had just been told. `qwen3.8-27b-mtplx` is
gone from the engine and 404s. The vision models earn their place on text alone: `qwen3-vl-4b` was the
quickest good answer of anything tested, and the vision tower simply sits idle on a text request.

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
