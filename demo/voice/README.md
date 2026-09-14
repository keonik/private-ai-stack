# Voice demo — speak to a Mac, hear it answer

A public page whose inference is private. The browser records audio and talks only to this app; this
app holds the key for the inference endpoint and forwards to it. Nothing is written to disk, and the
demo has no path to any document corpus — it exists to show the speech loop, not retrieval.

```
browser ──► this app (VPS) ──► private endpoint (a Mac) ──► transcribe → answer → speak
```

The page is React, built with Vite and Tailwind, using components from the AI SDK's Elements library —
**mic-selector**, **voice-selector** (with per-voice preview), **transcription** (click a phrase to hear
that moment of your own recording) and **audio-player**. `npm run dev` inside `web/` proxies the API to
a backend on :8085; the Dockerfile builds the page in a node stage and serves it from the python one, so
the runtime image carries no node.

Persona, the library's animated AI visual, was installed and then removed. It is a canned Rive
animation with five states and no amplitude input; the ring here uses the same five-state vocabulary but
drives four of them from real audio, which is the more honest picture and one less WebGL dependency.

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
and the **400 ms** before speech was detected is kept so the first word survives. While the reply plays
the gate either ignores the microphone or listens for you talking over it — see *The lab* below. The visualiser draws the opening threshold
as a pair of faint lines, so it is visible when a voice simply is not clearing it.

**Sensitivity** is a control on the page, because only the person in the room knows whether there is a
fan running. `vad_sim.js` replays the gate offline against synthetic speech at several volumes — the
gate cannot be tested from a terminal, and every bug above was found by replaying it rather than by
reading the code.

The browser sends 16 kHz mono WAV it encodes itself, so the server never has to decode a container
format. That removes the whole class of "every .webm upload 500s" failures, and 16 kHz is what every
backend resamples to anyway.

**Who has the floor** is one circular object with five states, borrowed from the AI SDK's Persona
component: `asleep`, `listening`, `hearing`, `thinking`, `speaking`. Seventy-two bars radiate from a
still centre, newest sound at the top, sweeping clockwise — so speech reads as a wave travelling around
the ring rather than a meter jumping up and down. Green while you talk, teal while the Mac answers.

The difference from Persona is that **four of the five states are driven by real amplitude**, not a
canned animation. Only `thinking` has nothing to listen to, so only that one is synthesised: two lobes
travelling around the same ring. That is deliberate — the loading state is the same object being busy,
rather than a spinner appearing next to it. The gate's opening threshold is drawn as a faint dashed
circle while listening, so it is visible when a quiet voice is not clearing it.

Your side comes from the RMS the voice detection already computes. The reply's side is **computed from
the WAV bytes**, not from the audio graph.

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

`npm test` inside `web/` drives a real browser (Playwright) over the controls. Those tests exist because
three bugs shipped that no terminal could see: a voice dialog that never opened, replies that were silent
on phones, and a speech gate that only fired for a shout. The dialog one is worth keeping in mind —
`VoiceSelector` **is** the dialog root, so nesting `VoiceSelectorDialog` inside it creates a second
dialog whose open state stays false and the palette silently never appears. The test was confirmed to
fail on that exact composition before being kept.

`env_test.js` checks the parser against a real reply from the engine, and `viz_test.js` renders
every state against a stub canvas to catch non-finite geometry and states that draw nothing — neither
is visible from a terminal otherwise.

**Conversation memory** is the last six turns, sent with each request. The server coerces every role to
user or assistant and truncates each turn, because the client is untrusted and an unbounded history is
a way to make someone else's GPU do free work.

## The lab: latency, interrupting, fillers, engines

Every idea for making the conversation feel faster is a switch on the page, so they can be tried by ear
rather than argued about, and each turn adds a row to a latency table. A turn is **one request**,
`/api/talk`, which transcribes, answers and speaks, streamed back as server-sent events.

**How the reply is spoken.** *Whole* is the original: write the full answer, synthesise it, play it.
*Sentence by sentence* cuts the answer as the model writes it. *Eager* (the default) lets the very first
chunk end at a comma, so the voice starts on a clause while the rest is still being written. Kokoro,
twelve turns each, seconds from the upload to the first sound:

| | whole | sentence | eager |
|---|---|---|---|
| first sound, median | 2.61 | 2.26 | **1.75** |

Two infrastructure fixes were worth more than any of that. **A kept-alive connection to the endpoint:**
a fresh one per call paid a TLS handshake through the tunnel every time, and one sentence of speech went
from 0.52 s to 0.20 s. **MP3 instead of WAV** for the reply: 21 KB a sentence instead of 154 KB, over two
networks. With both, Kokoro in eager mode reaches first sound in **1.32 s** (whole: 2.10 s).

**Synthesis is serial within a turn.** Sending every sentence to the engine at once looked parallel and
was not: they queue on the same GPU as the model still writing. On Qwen3-TTS the first sentence took
**5.5 s** instead of 1 s. One at a time, the first is never waiting behind the rest.

**Talking over the reply** stops it within a block of audio (~46 ms), aborts the request so the server
stops writing and speaking, and records only what was actually heard into the history, ending with a
dash. The hard part is the Mac's own voice coming back into the microphone. The reply's loudness is
known at every instant (it is decoded to draw the ring), so the gate is told how much echo to expect
right now and only opens for sound clearly above it — **2.2×** the expected echo, held for **260 ms**. How
much echo this room returns is learned while the reply plays and you are quiet, and shown on the page.
If you pause mid-thought and carry on before any of the answer has played, that is not an interruption:
what you said is carried into the next turn and answered as one question.

**Fillers** — "Hmm, let me think.", "Okay, so.", "Right.", "Good question.", "Let's see." — are synthesised
once per voice when the conversation starts and played only if no reply audio has arrived **600 ms** after
your turn ended (or 250 ms for a quick acknowledgement). Humans leave about 200 ms between turns;
published guidance puts the point where a filler is needed at 500-800 ms. On a fast turn you never hear
one.

**Speech engines.** All served by the same oMLX process. Three sentences per voice, each transcribed
back with Parakeet to catch a voice that mumbles; first sound is eager mode through the whole pipeline:

| engine | a sentence alone | first sound in a turn | word errors | licence |
|---|---|---|---|---|
| Kokoro 82M | 0.12-0.20 s | 1.32 s | 0% | Apache-2.0 |
| Pocket TTS (Kyutai) | 0.20-0.32 s | 1.59 s | 0-8% | CC-BY-4.0 |
| Chatterbox Turbo (Resemble) | 0.55 s | 1.92 s | 0% | MIT, inaudible watermark |
| VibeVoice Realtime (Microsoft) | 0.46-0.66 s | 1.97 s | 0-7% | MIT |
| Qwen3-TTS 0.6B | 0.8-1.2 s | 2.15 s | 0-10% | Apache-2.0 |
| Qwen3-TTS 1.7B | 0.9-1.1 s | 2.52 s | 0-7% | Apache-2.0 |

Left out: Qwen3-TTS *ryan* (97-114 words a minute, 13-15% errors) and *dylan* on the 1.7B (31%). Voxtral
TTS is non-commercial, oMLX does not serve Orpheus, and CSM needs gated access. The gateway rejects a
speech request with no `voice` at all, so Chatterbox, which has one built-in voice, is still sent a name.

`tests/conversation.spec.ts` runs whole conversations in Chromium with a **fake microphone playing a
recorded voice**: a plain turn, talking over a story until it stops and answers the new question, and
the slowest engine with fillers on. There is no room echo in a headless browser, so those prove the logic
of interrupting, not how it behaves on a laptop speaker — that part needs a person.

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

| who | limit |
|---|---|
| anyone | **45 requests per 5 minutes** per address, 2,000 per UTC day across everyone |
| tester pass | **300 per 5 minutes**, counted per pass rather than per address, and exempt from the daily budget |

A conversation turn is three requests: transcribe, answer, speak. The original per-address limit was 20,
about six turns — and one tester's session showed why it was wrong: **44 speech requests against 17
answers**, because clicking through the voice picker spent a request per preview.

**Voice previews are cached.** Every voice says the same sentence, so it is synthesised once and then
served from memory — 6 s cold, 1.7 ms after. A cached preview is not rate-limited at all.

**Tester passes** are for people you want to hand the demo to without them running into the limit.
`DEMO_PASSES="alice:<token>,bob:<token>"` in the deployment's environment; give someone
`https://…/?pass=<token>`. The page keeps the pass in that browser, **strips it from the address bar**
immediately so it does not end up in a screenshot, and sends it as `X-Demo-Pass`. Tokens are compared in
constant time, a pass holder is bucketed by name so switching from wifi to a phone does not reset them,
and a pass is deliberately not unlimited: a leaked link can use the demo heavily, but it cannot run the
GPU flat out. Revoke one by removing it from the variable and redeploying. `/api/whoami` reports which
tier a request is on.

Beyond those: a four megabyte upload ceiling (about two minutes of audio), four hundred characters of
text into the chat and speech steps, and a kill switch (`DEMO_ENABLED=0`). Give the demo its own API key
on the endpoint so it can be rate-limited and revoked without touching anything else.

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
