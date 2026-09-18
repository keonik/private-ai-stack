"""Voice demo: speak, get transcribed, get answered, hear the answer — all on one Mac.

The page is public; the inference is not. The browser talks only to this app, and this app holds the
key for the private endpoint. No audio and no transcript is written to disk, and nothing here can reach
the document corpus — this demo exists to show the speech path, not the retrieval one.

    uvicorn app:app --host 0.0.0.0 --port 8080

Environment:
    INFER_BASE_URL   OpenAI-compatible endpoint (required), e.g. https://example.com/v1
    INFER_API_KEY    key for it (required)
    STT_MODEL        default parakeet-tdt-0.6b-v2
    CHAT_MODEL       default qwen3.6-35b-a3b
    TTS_MODEL        default kokoro-tts
    DAILY_BUDGET     total requests served per UTC day before the demo closes (default 2000)
    DEMO_ENABLED     set to 0 to take it down without redeploying
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BASE = os.environ.get("INFER_BASE_URL", "").rstrip("/")
KEY = os.environ.get("INFER_API_KEY", "")
STT_MODEL = os.environ.get("STT_MODEL", "parakeet-tdt-0.6b-v2")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen3.6-35b-a3b")

# Chat models offered in the picker, with the per-model flag each one needs to stop it reading its own
# reasoning aloud. Median of voice-shaped questions, measured 2026-09-18 on a machine that was also running
# something heavy — the ranking holds, the absolute numbers are pessimistic:
#
#   qwen3.6-35b-a3b      0.71 s   enable_thinking=false   35B MoE, ~3B active: the best answers per second here
#   us-nemotron-30b-a3b  0.79 s   enable_thinking=false   0.97 s without the flag, and it reasons in the open
#   gemma4-e4b-mlx       1.25 s   nothing needed          terse
#   us-gemma4-26b-a4b    1.56 s   nothing needed
#
# Every Qwen generation ships thinking ON and writes it into `content`, not `reasoning_content` — so without
# the flag the assistant literally reads "Thinking Process: 1. Analyze the Request" aloud. Nemotron is the
# same. Left out: us-gpt-oss-120b, which oMLX refuses to load beside the voice models ("projected memory
# 103 GB would exceed the dynamic ceiling"), and the retired 27B builds, whose names the gateway still
# answers by routing them to the MoE.
CHAT_CHOICES = [
    {"id": "qwen3.6-35b-a3b",     "label": "Qwen3.6 35B MoE",   "note": "best answers, ~0.7 s",
     "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"id": "us-nemotron-30b-a3b", "label": "Nemotron 3.5 30B",  "note": "~0.8 s",
     "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"id": "gemma4-e4b-mlx",      "label": "Gemma 4 E4B",       "note": "~1.2 s, terse", "extra": {}},
    {"id": "us-gemma4-26b-a4b",   "label": "Gemma 4 26B MoE",   "note": "~1.6 s", "extra": {}},
]
CHAT_BY_ID = {c["id"]: c for c in CHAT_CHOICES}
TTS_MODEL = os.environ.get("TTS_MODEL", "kokoro-tts")
DAILY_BUDGET = int(os.environ.get("DAILY_BUDGET", "2000"))
ENABLED = os.environ.get("DEMO_ENABLED", "1") != "0"

MAX_AUDIO_BYTES = 4 * 1024 * 1024   # ~2 minutes of 16 kHz mono WAV
MAX_TEXT = 400
# A conversation turn costs three requests, so the old 20 allowed about six turns per five minutes —
# and a tester clicking through voice previews hit it long before that.
PER_IP = (45, 300)                   # 45 requests per 5 minutes per address
PASS_LIMIT = (300, 300)              # per pass holder: generous, but a leaked link still cannot run the GPU flat out

# Tester passes: DEMO_PASSES="gray:<token>,friend:<token>". A pass lifts the per-address limit and the
# daily budget for whoever holds the link. Tokens live only in the deployment's environment.
PASSES = {tok.strip(): name.strip()
          for name, _, tok in (pair.partition(":") for pair in os.environ.get("DEMO_PASSES", "").split(","))
          if name.strip() and tok.strip()}

# Kokoro ships 54 voice files; 41 of them synthesise on this engine. Every Japanese (jf_/jm_) and
# Chinese (zf_/zm_) voice returns 500 — those need misaki's ja/zh phonemizers, which the engine's
# environment does not carry. The list is checked in rather than discovered because this app runs on a
# different machine from the model directory, and offering a voice that 500s is worse than not
# offering it. Re-test with demo/voice/check_voices.sh if the engine's packages change.
VOICE_LANGS = {"a": "American English", "b": "British English", "e": "Spanish",
               "f": "French", "h": "Hindi", "i": "Italian", "p": "Brazilian Portuguese"}
VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "ef_dora", "em_alex", "em_santa", "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola",
    "pf_dora", "pm_alex", "pm_santa",
]
DEFAULT_VOICE = "af_heart"


SYSTEM_PROMPT = (
    "You are a voice assistant running on a Mac in someone's office. You are being spoken to out "
    "loud and your reply is read aloud, so answer in at most three sentences, plainly, with no "
    "lists and no markdown.\n"
    # Without this paragraph it offers to play soothing sounds and short stories, then plays
    # nothing, because speaking is the only thing it can actually do.
    "Talking is the only thing you can do. You cannot play music, sounds or recordings, set "
    "timers or reminders, send anything, search the web, open files, or control this computer. "
    "Never offer to do any of those, and never say you are about to play something. If someone "
    "asks for a story, a calming exercise or a joke, simply say it yourself, now, in your reply. "
    "If they ask for something you genuinely cannot do, say so in one sentence and offer what you "
    "can.\n"
    "If you are interrupted, the last assistant turn ends where the person cut you off; carry on "
    "from what they said, do not repeat yourself.\n"
    "If asked what you are, say you are an open-weight model running locally.")


def clean_history(raw) -> list[dict]:
    """The client is untrusted: last six turns, roles coerced, each capped."""
    out = []
    for turn in (raw or [])[-6:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = str(turn.get("content") or "").strip()[:MAX_TEXT]
        if content:
            out.append({"role": role, "content": content})
    return out


def voice_catalogue() -> list[dict]:
    """Grouped for a picker: language from the first letter, gender from the second."""
    out = []
    for v in VOICES:
        lang, gender = VOICE_LANGS.get(v[0], "Other"), "female" if v[1] == "f" else "male"
        out.append({"id": v, "label": v.split("_", 1)[1].title(), "language": lang, "gender": gender})
    return out


# Speech engines offered in the lab, all served by the same oMLX process. Measured on the reference machine
# 2026-09-14, three sentences per voice, each transcribed back with Parakeet to catch a voice that mumbles:
#
#   kokoro-tts          0.12-0.20 s a sentence   0% word errors    Apache-2.0
#   pocket-tts          0.20-0.32 s              0-8%              CC-BY-4.0, credit Kyutai
#   vibevoice-realtime  0.46-0.66 s              0-7%              MIT
#   chatterbox-turbo    0.55 s                   0%                MIT, adds an inaudible watermark
#   qwen3-tts-0.6b      0.8-1.2 s                0-10%             Apache-2.0
#   qwen3-tts-1.7b      0.9-1.1 s                0-7%              Apache-2.0
#
# Left out: Qwen3-TTS "ryan" on both sizes (97-114 words a minute and 13-15% word errors) and "dylan" on the
# 1.7B (31%). Voxtral TTS is non-commercial; Orpheus is not served by this engine; CSM needs gated access.
def _v(id_: str, label: str, language: str, gender: str) -> dict:
    return {"id": id_, "label": label, "language": language, "gender": gender}


_QWEN = [("vivian", "Vivian", "Chinese", "female"), ("serena", "Serena", "Chinese", "female"),
         ("uncle_fu", "Uncle Fu", "Chinese", "male"), ("eric", "Eric", "Chinese", "male"),
         ("dylan", "Dylan", "Chinese", "male"), ("aiden", "Aiden", "English", "male"),
         ("ono_anna", "Ono Anna", "Japanese", "female"), ("sohee", "Sohee", "Korean", "female")]
_VIBE_LANG = {"en": "English", "in": "Indian English", "de": "German", "fr": "French", "it": "Italian",
              "jp": "Japanese", "kr": "Korean", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese", "sp": "Spanish"}
_VIBE = ["en-Carter_man", "en-Davis_man", "en-Emma_woman", "en-Frank_man", "en-Grace_woman", "en-Mike_man",
         "in-Samuel_man", "de-Spk0_man", "de-Spk1_woman", "fr-Spk0_man", "fr-Spk1_woman", "it-Spk0_woman",
         "it-Spk1_man", "jp-Spk0_man", "jp-Spk1_woman", "kr-Spk0_woman", "kr-Spk1_man", "nl-Spk0_man",
         "nl-Spk1_woman", "pl-Spk0_man", "pl-Spk1_woman", "pt-Spk0_woman", "pt-Spk1_man", "sp-Spk0_woman",
         "sp-Spk1_man"]


def _vibe(v: str) -> dict:
    lang, rest = v.split("-", 1)
    name, gender = rest.rsplit("_", 1)
    label = name if not name.startswith("Spk") else f"Speaker {int(name[3:]) + 1}"
    return _v(v, label, _VIBE_LANG.get(lang, lang), "female" if gender == "woman" else "male")


TTS_ENGINES = [
    {"id": "kokoro-tts", "label": "Kokoro 82M", "note": "fastest, ~0.15 s a sentence", "default": "af_heart",
     "voices": voice_catalogue()},
    {"id": "pocket-tts", "label": "Pocket TTS · Kyutai", "note": "~0.25 s a sentence", "default": "alba",
     "voices": [_v(n, n.title(), "English", g) for n, g in (
         ("alba", "female"), ("azelma", "female"), ("cosette", "female"), ("eponine", "female"),
         ("fantine", "female"), ("javert", "male"), ("jean", "male"), ("marius", "male"))]},
    {"id": "vibevoice-realtime", "label": "VibeVoice Realtime · Microsoft", "note": "~0.55 s a sentence",
     "default": "en-Emma_woman", "voices": [_vibe(v) for v in _VIBE]},
    {"id": "chatterbox-turbo", "label": "Chatterbox Turbo · Resemble", "note": "expressive, ~0.55 s",
     "default": "default", "voices": [_v("default", "Default", "English", "female")]},
    {"id": "qwen3-tts-1.7b", "label": "Qwen3-TTS 1.7B", "note": "~1 s a sentence", "default": "vivian",
     "voices": [_v(*q) for q in _QWEN if q[0] != "dylan"]},
    {"id": "qwen3-tts-0.6b", "label": "Qwen3-TTS 0.6B", "note": "~1 s a sentence", "default": "vivian",
     "voices": [_v(*q) for q in _QWEN]},
]
ENGINE_BY_ID = {e["id"]: e for e in TTS_ENGINES}


def pick_voice(engine: str | None, voice: str | None) -> tuple[str, str]:
    """A known engine and one of its voices, or a 400 — the engine answers unknown voices with a 500 and a path."""
    eng = ENGINE_BY_ID.get(engine or "") or ENGINE_BY_ID[TTS_MODEL if TTS_MODEL in ENGINE_BY_ID else "kokoro-tts"]
    voice = voice or eng["default"]
    if voice not in {v["id"] for v in eng["voices"]}:
        raise HTTPException(400, "That voice is not available.")
    return eng["id"], voice


def speech_body(engine: str, voice: str, text: str, fmt: str) -> dict:
    # Chatterbox has one built-in voice and ignores the name, but the gateway rejects a speech request that
    # has no voice at all ("Router.aspeech() missing 1 required positional argument"), so always send one.
    return {"model": engine, "input": text, "response_format": fmt, "voice": voice}


# Smart Turn runs here, on this app's CPU, not on the inference machine: it is 8.7 MB and ~40 ms, and the
# audio is already in this process. If the model or onnxruntime is missing the page falls back to pauses.
SMART_TURN_MODEL = os.environ.get("SMART_TURN_MODEL", str(Path(__file__).parent / "smart-turn-v3.2-cpu.onnx"))
INCOMPLETE_HOLD_S = 0.6    # after "not finished": wait this long before transcribing, in case you carry on
_smart_turn = None


def smart_turn():
    global _smart_turn
    if _smart_turn is None:
        try:
            from smart_turn import SmartTurn
            _smart_turn = SmartTurn(SMART_TURN_MODEL)
        except Exception as e:  # noqa: BLE001 — any failure here just means "use pauses"
            _smart_turn = e
    return None if isinstance(_smart_turn, Exception) else _smart_turn


# Talking over the reply: short sounds that mean "I'm listening", not "stop". Measured against Parakeet on
# synthetic clips — it writes "Yeah." for most very short sounds, and anything cut to under half a second
# (including "What about the moon?") came back as "Yeah." or "Okay.", which is why the page waits for the
# sound to end before asking, and treats more than 0.9 s of voice as an interruption without asking at all.
BACKCHANNELS = {"mm", "mmm", "hmm", "hm", "mhm", "mm-hmm", "mmhmm", "uh-huh", "uhhuh", "uh", "um", "ah", "oh",
                "yeah", "yea", "yep", "yup", "yes", "ok", "okay", "right", "sure", "cool", "nice", "wow",
                "gotcha", "true", "exactly", "totally", "i", "see", "got", "it", "alright", "aha"}


def backchannel_verdict(text: str, said: str = "") -> str:
    """What was that sound: the Mac's own voice coming back, "mm-hmm", noise, or someone interrupting?"""
    words = re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower())
    if not words:
        return "ambient"
    # Echo first. On laptop speakers the reply leaks into the microphone, the browser's echo cancelling does
    # not always catch it, and transcribing that leak gives real words — which looked exactly like someone
    # talking over the answer. The answer's own words are known here, so a "sound" made of them is the Mac.
    mine = set(re.findall(r"[a-z]+(?:-[a-z]+)?", said.lower()))
    if mine and len(words) >= 2:
        overlap = sum(1 for w in words if w in mine) / len(words)
        if overlap >= 0.6:
            return "echo"
    if len(words) <= 3 and all(w in BACKCHANNELS for w in words):
        return "backchannel"
    return "interrupt"


# ---------------------------------------------------------------------------------------------------------
# Metrics, for Prometheus. Only numbers and enums: no transcripts, no voices, no addresses — the page promises
# nothing is kept, and a latency histogram does not need to know what anyone said.
class _Hist:
    def __init__(self, buckets):
        self.buckets, self.series = buckets, {}

    def observe(self, labels: tuple, v: float) -> None:
        counts, total = self.series.setdefault(labels, ([0] * len(self.buckets), [0.0, 0]))
        for i, b in enumerate(self.buckets):
            if v <= b:
                counts[i] += 1
        total[0] += v
        total[1] += 1


LATENCY_BUCKETS = (0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 6, 10)
METRIC_HISTS = {
    "voice_answer_heard_seconds": (_Hist(LATENCY_BUCKETS), ("reply", "engine", "turn"),
                                   "From the end of your speech to the first word of the answer"),
    "voice_first_sound_seconds": (_Hist(LATENCY_BUCKETS), ("turn", "fillers"),
                                  "From the end of your speech to the first sound, filler or answer"),
    "voice_stage_seconds": (_Hist(LATENCY_BUCKETS), ("stage",), "Pipeline stages, from the end-of-turn decision"),
    "voice_smart_turn_probability": (_Hist((0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1)), (),
                                     "Smart Turn's probability that a pause ended the turn"),
    "voice_smart_turn_inference_seconds": (_Hist((0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.15, 0.25, 0.5)), (),
                                           "Smart Turn model time on this app's CPU"),
}
METRIC_COUNTS: dict[str, tuple[dict, tuple, str]] = {
    "voice_turns_total": ({}, ("outcome", "reply", "engine", "turn", "barge"), "Turns by how they ended"),
    "voice_backchannel_total": ({}, ("verdict",), "Sounds made over the reply, by verdict"),
    "voice_smart_turn_total": ({}, ("verdict",), "Smart Turn decisions"),
    "voice_rate_limited_total": ({}, ("tier",), "Requests refused by the demo's own limits"),
}


def count(name: str, *labels: str) -> None:
    series = METRIC_COUNTS[name][0]
    series[labels] = series.get(labels, 0) + 1


def observe(name: str, value: float, *labels: str) -> None:
    METRIC_HISTS[name][0].observe(labels, value)


def _lbl(names, values, extra: str = "") -> str:
    parts = [f'{n}="{v}"' for n, v in zip(names, values)] + ([extra] if extra else [])
    return "{" + ",".join(parts) + "}" if parts else ""


def render_metrics() -> str:
    out = []
    for name, (series, names, help_) in METRIC_COUNTS.items():
        out += [f"# HELP {name} {help_}", f"# TYPE {name} counter"]
        out += [f"{name}{_lbl(names, labels)} {v}" for labels, v in sorted(series.items())]
    for name, (h, names, help_) in METRIC_HISTS.items():
        out += [f"# HELP {name} {help_}", f"# TYPE {name} histogram"]
        for labels, (counts, (total, n)) in sorted(h.series.items()):
            out += [f"{name}_bucket{_lbl(names, labels, f'le="{b}"')} {c}" for b, c in zip(h.buckets, counts)]
            out += [f"{name}_bucket{_lbl(names, labels, 'le="+Inf"')} {n}",
                    f"{name}_sum{_lbl(names, labels)} {total}", f"{name}_count{_lbl(names, labels)} {n}"]
    out += ["# TYPE voice_requests_served_today gauge", f"voice_requests_served_today {_day[1]}"]
    return "\n".join(out) + "\n"


_hits: dict[str, deque] = defaultdict(deque)
_day = ["", 0]

app = FastAPI(title="voice demo", docs_url=None, redoc_url=None)
STATIC = Path(__file__).parent / "static"


def pass_holder(request: Request) -> str | None:
    """The name behind a valid tester pass, compared in constant time so a token cannot be guessed by timing."""
    import hmac
    offered = request.headers.get("x-demo-pass", "")
    if not offered:
        return None
    for token, name in PASSES.items():
        if hmac.compare_digest(offered.encode(), token.encode()):
            return name
    return None


def guard(request: Request, cost: int = 1) -> None:
    """Cheap protection for a public page pointed at someone's GPU. `cost` is how many requests this counts as."""
    if not ENABLED:
        raise HTTPException(503, "The demo is switched off right now.")
    if not BASE or not KEY:
        raise HTTPException(500, "The demo is not configured with an inference endpoint.")
    holder = pass_holder(request)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _day[0] != today:
        _day[0], _day[1] = today, 0
    if _day[1] >= DAILY_BUDGET and not holder:
        count("voice_rate_limited_total", "daily")
        raise HTTPException(429, "The demo has used its budget for today. It resets at midnight UTC.")
    _day[1] += cost

    ip = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
          or (request.client.host if request.client else "?")).strip()
    # A pass holder is counted by name, not address, so moving from wifi to a phone does not reset them
    # and two testers behind the same office NAT do not share a bucket.
    bucket, (limit, window) = (f"pass:{holder}", PASS_LIMIT) if holder else (ip, PER_IP)
    now = time.time()
    q = _hits[bucket]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) + cost > limit:
        count("voice_rate_limited_total", "pass" if holder else "anonymous")
        raise HTTPException(429, "That is a lot of requests. Give it a minute.")
    q.extend([now] * cost)


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    """One long-lived connection pool to the endpoint.

    A fresh client per call paid a TLS handshake through the tunnel every time: measured 0.52 s median for
    one sentence of speech that way, against 0.20 s on a kept-alive connection. Most of a voice turn's
    budget was being spent saying hello.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=120, headers={"Authorization": f"Bearer {KEY}",
                                                          "User-Agent": "voice-demo/1.0"},
                                    limits=httpx.Limits(max_keepalive_connections=16, keepalive_expiry=300))
    return _client


async def upstream(method: str, path: str, **kw) -> httpx.Response:
    r = await client().request(method, f"{BASE}{path}", **kw)
    if r.status_code >= 400:
        raise HTTPException(502, f"The inference endpoint returned {r.status_code}.")
    return r


@app.on_event("startup")
async def warm_forever() -> None:
    """Keep the three models resident.

    oMLX pools models and evicts the least recently used, so the first visitor after a quiet spell pays
    the load: measured 3.65 s to synthesise a five-second clip cold against 1.19 s warm, and 4.09 s to
    transcribe it against 0.49 s. A demo that is slow exactly when someone new arrives is not a demo.
    One cheap round trip every few minutes keeps all three hot; the TTS warm-up produces the audio that
    warms transcription, so it costs one synthesis rather than a stored fixture.
    """
    import asyncio

    async def loop() -> None:
        while True:
            try:
                if BASE and KEY and ENABLED:
                    r = await upstream("POST", "/audio/speech",
                                       json={"model": TTS_MODEL, "input": "ready", "voice": "af_heart",
                                             "response_format": "wav"})
                    await upstream("POST", "/audio/transcriptions",
                                   files={"file": ("warm.wav", r.content, "audio/wav")},
                                   data={"model": STT_MODEL})
                    await upstream("POST", "/chat/completions",
                                   json={"model": CHAT_MODEL, "max_tokens": 1,
                                         "messages": [{"role": "user", "content": "hi"}]})
            except Exception:
                pass  # the demo degrades to a cold start, which is not worth crashing over
            await asyncio.sleep(240)

    asyncio.create_task(loop())
    asyncio.create_task(asyncio.to_thread(smart_turn))


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/vite.svg", include_in_schema=False)
def favicon() -> Response:
    f = STATIC / "vite.svg"
    return FileResponse(f) if f.exists() else Response(status_code=404)


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True, "enabled": ENABLED, "configured": bool(BASE and KEY),
                         "served_today": _day[1], "budget": DAILY_BUDGET, "smart_turn": smart_turn() is not None,
                         "models": {"stt": STT_MODEL, "chat": CHAT_MODEL, "tts": TTS_MODEL}})


PREVIEW_TEXT = "This is how I sound."
_previews: dict[tuple[str, str], bytes] = {}


@app.get("/api/preview")
async def preview(request: Request, voice: str = Query(...), engine: str = Query("kokoro-tts")) -> Response:
    """The same sentence in every voice, synthesised once and then served from memory.

    Previews were most of the traffic that tripped the rate limit — 27 of 44 speech requests in one
    tester's session — while costing the GPU the same work every time for a sentence that never changes.
    A cached preview is not rate-limited: it touches nothing but this process's memory.
    """
    engine, voice = pick_voice(engine, voice)
    if (engine, voice) not in _previews:
        guard(request)
        r = await upstream("POST", "/audio/speech", json=speech_body(engine, voice, PREVIEW_TEXT, "mp3"))
        _previews[(engine, voice)] = r.content
    return Response(_previews[(engine, voice)], media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/whoami")
def whoami(request: Request) -> JSONResponse:
    holder = pass_holder(request)
    limit, window = PASS_LIMIT if holder else PER_IP
    return JSONResponse({"pass": holder, "limit": limit, "window_seconds": window})


@app.get("/api/models")
def models() -> JSONResponse:
    default = CHAT_MODEL if CHAT_MODEL in CHAT_BY_ID else CHAT_CHOICES[0]["id"]
    return JSONResponse({"default": default,
                         "models": [{k: c[k] for k in ("id", "label", "note")} for c in CHAT_CHOICES]})


@app.get("/api/voices")
def voices(engine: str = Query("kokoro-tts")) -> JSONResponse:
    eng = ENGINE_BY_ID.get(engine) or ENGINE_BY_ID["kokoro-tts"]
    return JSONResponse({"engine": eng["id"], "default": eng["default"], "voices": eng["voices"]})


@app.get("/api/engines")
def engines() -> JSONResponse:
    return JSONResponse({"default": "kokoro-tts",
                         "engines": [{k: e[k] for k in ("id", "label", "note", "default")} for e in TTS_ENGINES]})


@app.post("/api/transcribe")
async def transcribe(request: Request, audio: UploadFile, seconds: float = Form(0.0)) -> JSONResponse:
    guard(request)
    blob = await audio.read()
    if len(blob) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That clip is too long for the demo. Keep it under about two minutes.")
    t0 = time.time()
    r = await upstream("POST", "/audio/transcriptions",
                       files={"file": ("clip.wav", blob, "audio/wav")},
                       data={"model": STT_MODEL})
    took = time.time() - t0
    d = r.json()
    text = (d.get("text") or "").strip()
    # The engine returns per-sentence bounds; passing them through is what lets the page offer
    # click-a-phrase-to-hear-it on the recording, which never leaves the browser.
    segments = [{"text": (seg.get("text") or "").strip(),
                 "startSecond": float(seg.get("start") or 0.0),
                 "endSecond": float(seg.get("end") or 0.0)}
                for seg in (d.get("segments") or []) if (seg.get("text") or "").strip()]
    return JSONResponse({"text": text, "segments": segments, "seconds": round(took, 2),
                         "audio_seconds": round(seconds, 2),
                         "realtime": round(seconds / took, 1) if took > 0 and seconds else None,
                         "model": STT_MODEL})


@app.post("/api/reply")
async def reply(request: Request, payload: dict) -> JSONResponse:
    guard(request)
    text = (payload.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        raise HTTPException(400, "Nothing to answer.")
    # A spoken conversation needs the last few turns or every answer restarts from nothing. Capped hard:
    # the client is untrusted, and an unbounded history is a way to make someone else's GPU do free work.
    history = clean_history(payload.get("history"))
    choice = CHAT_BY_ID.get(payload.get("model") or "") or CHAT_BY_ID.get(CHAT_MODEL) or CHAT_CHOICES[0]
    t0 = time.time()
    r = await upstream("POST", "/chat/completions", json={
        "model": choice["id"], "max_tokens": 220, "temperature": 0.4, **choice["extra"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": text}]})
    took = time.time() - t0
    # Never read reasoning_content aloud: on a thinking model that is the scratchpad, and when the
    # budget runs out mid-thought it is all there is. Better to say nothing than to narrate deliberation.
    msg = (r.json()["choices"][0]["message"].get("content") or "").strip()
    if not msg:
        raise HTTPException(502, "That model spent its budget thinking and said nothing. Try another one.")
    return JSONResponse({"text": msg, "seconds": round(took, 2), "model": choice["id"]})


@app.post("/api/speak")
async def speak(request: Request, payload: dict) -> Response:
    guard(request)
    text = (payload.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        raise HTTPException(400, "Nothing to say.")
    voice = payload.get("voice") or DEFAULT_VOICE
    if voice not in VOICES:
        # The engine answers an unknown voice with a 500 quoting a filesystem path, so catch it here.
        raise HTTPException(400, "That voice is not available.")
    t0 = time.time()
    r = await upstream("POST", "/audio/speech", json={"model": TTS_MODEL, "input": text,
                                                      "voice": voice, "response_format": "wav"})
    return Response(r.content, media_type="audio/wav",
                    headers={"X-Synthesis-Seconds": str(round(time.time() - t0, 2))})


# ---------------------------------------------------------------------------------------------------------
# Fillers: a few short phrases per voice, synthesised once and served from memory like the previews. The
# page plays one only when the real answer is slow to start, so on a fast turn you never hear one.
FILLERS = ["Hmm, let me think.", "Okay, so.", "Right.", "Good question.", "Let's see."]
_fillers: dict[tuple[str, str, int], bytes] = {}


@app.get("/api/fillers")
def fillers() -> JSONResponse:
    return JSONResponse({"fillers": FILLERS})


@app.get("/api/filler")
async def filler(request: Request, voice: str = Query(...), i: int = Query(0),
                 engine: str = Query("kokoro-tts")) -> Response:
    if not 0 <= i < len(FILLERS):
        raise HTTPException(400, "No such filler.")
    engine, voice = pick_voice(engine, voice)
    if (engine, voice, i) not in _fillers:
        guard(request)
        r = await upstream("POST", "/audio/speech", json=speech_body(engine, voice, FILLERS[i], "mp3"))
        _fillers[(engine, voice, i)] = r.content
    return Response(_fillers[(engine, voice, i)], media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/backchannel")
async def backchannel(request: Request, audio: UploadFile, said: str = Form("")) -> JSONResponse:
    """Was that sound, made while the Mac was talking, an interruption, its own echo, or just "mm-hmm"?"""
    guard(request)
    said = said[:2000]
    blob = await audio.read()
    if len(blob) > MAX_AUDIO_BYTES // 8:
        return JSONResponse({"verdict": "interrupt", "text": "", "reason": "long"})
    t0 = time.time()
    r = await upstream("POST", "/audio/transcriptions", files={"file": ("clip.wav", blob, "audio/wav")},
                       data={"model": STT_MODEL})
    text = (r.json().get("text") or "").strip()
    verdict = backchannel_verdict(text, said)
    count("voice_backchannel_total", verdict)
    return JSONResponse({"verdict": verdict, "text": text, "seconds": round(time.time() - t0, 3)})


ENUMS = {"reply": {"eager", "stream", "whole"}, "turn": {"smart", "pause"}, "barge": {"smart", "instant", "off"},
         "fillers": {"off", "tone", "words"},
         "outcome": {"done", "interrupted", "continued", "reopened", "ignored", "timeout", "error"},
         "engine": {e["id"] for e in TTS_ENGINES}}
_metric_posts: dict[str, deque] = defaultdict(deque)


@app.post("/api/lab-metrics")
async def lab_metrics(request: Request, payload: dict) -> Response:
    """The page reports how each turn went. Numbers and enums only; anything else is dropped."""
    ip = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
          or (request.client.host if request.client else "?")).strip()
    q, now = _metric_posts[ip], time.time()
    while q and now - q[0] > 300:
        q.popleft()
    if len(q) >= 200:  # silently: a metrics beacon is not worth an error
        return Response(status_code=204)
    q.append(now)

    def enum(k: str) -> str:
        v = str(payload.get(k) or "")
        return v if v in ENUMS[k] else "other"

    def secs(k: str) -> float | None:
        try:
            v = float(payload.get(k))
        except (TypeError, ValueError):
            return None
        return v if 0 <= v <= 60 else None

    outcome, reply, engine, turn = enum("outcome"), enum("reply"), enum("engine"), enum("turn")
    count("voice_turns_total", outcome, reply, engine, turn, enum("barge"))
    silence = secs("silence") or 0.0
    answer, filler = secs("firstSound"), secs("filler")
    if answer is not None:
        observe("voice_answer_heard_seconds", answer + silence, reply, engine, turn)
    first = min(x for x in (answer, filler, 99.0) if x is not None)
    if first < 99:
        observe("voice_first_sound_seconds", first + silence, turn, enum("fillers"))
    for stage, key in (("heard", "heard"), ("first_words", "firstWords")):
        if (v := secs(key)) is not None:
            observe("voice_stage_seconds", v, stage)
    return Response(status_code=204)


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    """Prometheus. Answered directly on the machine; through a proxy only with METRICS_TOKEN as a bearer."""
    import hmac
    token = os.environ.get("METRICS_TOKEN", "")
    proxied = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    offered = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if proxied and not (token and hmac.compare_digest(offered.encode(), token.encode())):
        raise HTTPException(404)
    return Response(render_metrics(), media_type="text/plain; version=0.0.4")


# ---------------------------------------------------------------------------------------------------------
# One request per turn: transcribe, answer, speak, streamed back as server-sent events.
#
# In "stream" mode the answer is cut into sentences as the model writes it and each sentence goes to speech
# the moment it is complete, so the first sentence is playing while the third is still being written. In
# "whole" mode the answer is written in full and spoken in one go — the original behaviour, kept so the two
# can be compared on the same code path. Measured through the public endpoint on a warm model: the first
# sentence of a three-sentence answer is written after ~0.6 s and spoken in ~0.4 s, against 1.9 s for the
# whole answer plus 0.7 s to speak it.

SENTENCE_END = re.compile(r'([.!?]["\')\]]?)\s+')
MIN_CHUNK = 14       # "Sure." on its own is a clipped, odd-sounding clip; fold it into the next sentence
EAGER_MIN = 24       # an eager first clause must still be long enough to carry some intonation
LONG_CHUNK = 160     # a run-on sentence still gets cut at a comma rather than waiting for its full stop


def take_sentences(buf: str, final: bool = False, eager: bool = False) -> tuple[list[str], str]:
    """Pull finished sentences off the front of `buf`. `eager` lets the very first chunk end at a comma, so
    the voice can start on a clause while the rest of the sentence is still being written."""
    out = []
    while True:
        if eager and not out and ", " in buf[EAGER_MIN:]:
            cut = buf.index(", ", EAGER_MIN) + 1
            out.append(buf[:cut].strip())
            buf = buf[cut:].lstrip()
            eager = False
            continue
        # A full stop only ends a sentence if what follows does not start lower-case: that keeps "e.g. a
        # small one" and '"Really?" she asked' whole. Until the next word arrives, it waits.
        m = next((m for m in SENTENCE_END.finditer(buf)
                  if m.end(1) >= MIN_CHUNK and m.end() < len(buf) and not buf[m.end()].islower()), None)
        if m:
            out.append(buf[:m.end(1)].strip())
            buf = buf[m.end():]
            continue
        if len(buf) > LONG_CHUNK and ", " in buf[40:LONG_CHUNK]:
            cut = buf.rindex(", ", 40, LONG_CHUNK) + 1
            out.append(buf[:cut].strip())
            buf = buf[cut:].lstrip()
            continue
        break
    if final and buf.strip():
        out.append(buf.strip())
        buf = ""
    return out, buf


def speakable(text: str) -> str:
    """The prompt says no markdown; the model does not always listen, and Kokoro reads asterisks aloud."""
    return re.sub(r"[*_#`]+", "", text).strip()


def sse(kind: str, **data) -> bytes:
    return f"data: {json.dumps({'type': kind, **data})}\n\n".encode()


@app.post("/api/talk")
async def talk(request: Request, meta: str = Form("{}"), audio: UploadFile | None = None):
    from fastapi.responses import StreamingResponse

    # One request now, two more once the turn is really answered: a turn that is cancelled because you carried
    # on talking should not cost you three requests' worth of limit.
    guard(request, cost=1)
    blob = await audio.read() if audio is not None else b""
    if len(blob) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That clip is too long for the demo. Keep it under about two minutes.")
    try:
        opts = json.loads(meta)
    except ValueError:
        opts = {}
    # Typed instead of spoken: same pipeline, minus transcription. The answer is still spoken aloud.
    typed = str(opts.get("text") or "").strip()[:MAX_TEXT]
    if not blob and not typed:
        raise HTTPException(400, "Nothing to answer.")
    engine, voice = pick_voice(opts.get("engine"), opts.get("voice"))
    choice = CHAT_BY_ID.get(opts.get("model") or "") or CHAT_BY_ID.get(CHAT_MODEL) or CHAT_CHOICES[0]
    mode = {"whole": "whole", "eager": "eager"}.get(opts.get("mode"), "stream")
    history = clean_history(opts.get("history"))
    # Speech from a turn that was cut off before it was answered, so "what is the tallest… (pause) …
    # mountain in Ohio" is answered as one question rather than two.
    carry = str(opts.get("carry") or "").strip()[:MAX_TEXT]
    seconds = float(opts.get("seconds") or 0)
    use_smart = opts.get("turn") == "smart"
    async def events():
        t0 = time.time()
        since = lambda: round(time.time() - t0, 3)
        http = client()
        tasks: list[asyncio.Task] = []
        try:
            # Transcription starts now, alongside the end-of-turn decision rather than after it: it is ~0.1 s of
            # GPU, and a turn that turns out to continue just throws the result away. On an "unfinished"
            # verdict the text is ready by the time the 600 ms wait ends.
            stt_task = None
            if blob:
                stt_task = asyncio.create_task(http.post(f"{BASE}/audio/transcriptions", data={"model": STT_MODEL},
                                                         files={"file": ("clip.wav", blob, "audio/wav")}))
                tasks.append(stt_task)
            if blob and use_smart:
                st = smart_turn()
                if st is None:
                    yield sse("turn", complete=True, probability=None, ms=0, available=False)
                else:
                    try:
                        from smart_turn import pcm16_wav
                        verdict = await asyncio.to_thread(st.predict, pcm16_wav(blob))
                    except Exception:  # noqa: BLE001 — a clip the model cannot read is answered, not dropped
                        verdict = {"complete": True, "probability": None, "ms": 0}
                    yield sse("turn", **verdict, available=True, at=since())
                    count("voice_smart_turn_total", "complete" if verdict["complete"] else "incomplete")
                    if verdict["probability"] is not None:
                        observe("voice_smart_turn_probability", verdict["probability"])
                        observe("voice_smart_turn_inference_seconds", verdict["ms"] / 1000)
                    if not verdict["complete"]:
                        # Not finished, by the sound of it. Wait before spending GPU on it; if you carry on,
                        # the page cancels this request and sends the longer clip instead.
                        await asyncio.sleep(INCOMPLETE_HOLD_S)
            try:
                guard(request, cost=2)
            except HTTPException as e:
                yield sse("error", detail=e.detail)
                return
            yield sse("proceed", at=since())
            if stt_task is None:
                heard, segments = typed, []
                yield sse("heard", text=heard, segments=[], at=since(), realtime=None, model="typed", typed=True)
            else:
                r = await stt_task
                if r.status_code >= 400:
                    yield sse("error", detail=f"Transcription failed ({r.status_code}).")
                    return
                d = r.json()
                heard = (d.get("text") or "").strip()
                segments = [{"text": (g.get("text") or "").strip(), "startSecond": float(g.get("start") or 0),
                             "endSecond": float(g.get("end") or 0)}
                            for g in (d.get("segments") or []) if (g.get("text") or "").strip()]
                stt = since()
                yield sse("heard", text=heard, segments=segments, at=stt,
                          realtime=round(seconds / stt, 1) if seconds and stt else None, model=STT_MODEL)
            if not heard:
                yield sse("done", at=since())
                return
            question = f"{carry} {heard}".strip()[:MAX_TEXT]

            out: asyncio.Queue = asyncio.Queue()
            spoken: asyncio.Queue = asyncio.Queue()

            # One sentence at a time. Sending all of them at once looked parallel and was not: they queue on
            # the same GPU as the model still writing, and on the heavier engines the first sentence took 5.5 s
            # instead of 1 s because it was competing with the second and third.
            last: list[asyncio.Task | None] = [None]

            async def synth(text: str, before: asyncio.Task | None) -> tuple[bytes, float]:
                if before is not None:
                    await asyncio.gather(before, return_exceptions=True)
                s0 = time.time()
                # MP3, not WAV: a sentence is 21 KB instead of 154 KB, and it crosses two networks to get here.
                rr = await http.post(f"{BASE}/audio/speech", json=speech_body(engine, voice, speakable(text), "mp3"))
                rr.raise_for_status()
                return rr.content, round(time.time() - s0, 3)

            async def write() -> None:
                """Read the model's stream, hand each finished sentence to speech straight away."""
                buf, full, n = "", "", 0
                body = {"model": choice["id"], "max_tokens": 220, "temperature": 0.4, "stream": True,
                        **choice["extra"],
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history,
                                     {"role": "user", "content": question}]}
                try:
                    async with http.stream("POST", f"{BASE}/chat/completions", json=body) as resp:
                        if resp.status_code >= 400:
                            await out.put(sse("error", detail=f"The model returned {resp.status_code}."))
                            return
                        first = True
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                                continue
                            try:
                                delta = (json.loads(line[5:])["choices"] or [{}])[0].get("delta") or {}
                            except (ValueError, KeyError, IndexError):
                                continue
                            piece = delta.get("content") or ""  # never reasoning_content
                            if not piece:
                                continue
                            if first:
                                await out.put(sse("first_token", at=since()))
                                first = False
                            buf += piece
                            full += piece
                            if mode != "whole":
                                ready, buf = take_sentences(buf, eager=mode == "eager" and n == 0)
                                for sentence in ready:
                                    await out.put(sse("sentence", i=n, text=sentence, at=since()))
                                    last[0] = asyncio.create_task(synth(sentence, last[0]))
                                    tasks.append(last[0])
                                    await spoken.put((n, sentence, last[0]))
                                    n += 1
                    if mode != "whole":
                        ready, _ = take_sentences(buf, final=True)
                    else:
                        ready = [full.strip()] if full.strip() else []
                    for sentence in ready:
                        await out.put(sse("sentence", i=n, text=sentence, at=since()))
                        last[0] = asyncio.create_task(synth(sentence, last[0]))
                        tasks.append(last[0])
                        await spoken.put((n, sentence, last[0]))
                        n += 1
                    if not full.strip():
                        await out.put(sse("error", detail="That model spent its budget thinking and said nothing."))
                    await out.put(sse("written", text=full.strip(), at=since(), model=choice["id"]))
                finally:
                    await spoken.put(None)

            async def say() -> None:
                """Emit the audio strictly in order, however the syntheses finish."""
                try:
                    while (item := await spoken.get()) is not None:
                        i, sentence, task = item
                        try:
                            wav, took = await task
                        except Exception:
                            await out.put(sse("error", detail="Speech synthesis failed for one sentence."))
                            continue
                        await out.put(sse("audio", i=i, mp3=base64.b64encode(wav).decode(), synth=took,
                                          at=since()))
                finally:
                    await out.put(None)

            tasks += [asyncio.create_task(write()), asyncio.create_task(say())]
            while (event := await out.get()) is not None:
                yield event
            yield sse("done", at=since(), mode=mode, engine=engine)
        finally:
            # The page aborts this request when you talk over the answer; stop writing and speaking then too.
            for t in tasks:
                t.cancel()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
