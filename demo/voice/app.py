"""Voice demo: speak, get transcribed, get answered, hear the answer — all on one Mac.

The page is public; the inference is not. The browser talks only to this app, and this app holds the
key for the private endpoint. No audio and no transcript is written to disk, and nothing here can reach
the document corpus — this demo exists to show the speech path, not the retrieval one.

    uvicorn app:app --host 0.0.0.0 --port 8080

Environment:
    INFER_BASE_URL   OpenAI-compatible endpoint (required), e.g. https://example.com/v1
    INFER_API_KEY    key for it (required)
    STT_MODEL        default parakeet-tdt-0.6b-v2
    CHAT_MODEL       default gemma4-e4b-mlx
    TTS_MODEL        default kokoro-tts
    DAILY_BUDGET     total requests served per UTC day before the demo closes (default 2000)
    DEMO_ENABLED     set to 0 to take it down without redeploying
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

BASE = os.environ.get("INFER_BASE_URL", "").rstrip("/")
KEY = os.environ.get("INFER_API_KEY", "")
STT_MODEL = os.environ.get("STT_MODEL", "parakeet-tdt-0.6b-v2")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen3.5-9b")

# Chat models offered in the picker, with the per-model flag each one needs to stop it reading its own
# reasoning aloud. Median of four voice-shaped questions, measured on the reference machine 2026-09-13:
#
#   qwen3-vl-4b    0.51 s   nothing needed
#   qwen3.5-4b     0.59 s   enable_thinking=false
#   gemma4-e4b     0.61 s   nothing needed          terse; the original "odd helpfulness" complaint
#   qwen3.5-9b     0.87 s   enable_thinking=false   best answers per second measured
#   gpt-oss-20b    2.5 s    reasoning_effort=low    else thinking eats the budget and content is empty
#   Qwen3.8-27B    3.0 s    enable_thinking=false   else 10 s and it says "We need answer user's question:"
#
# Every Qwen generation here ships thinking ON by default and writes it into `content`, not
# `reasoning_content` — so without the flag the assistant literally reads "Thinking Process: 1. Analyze
# the Request" aloud. Phi-4-mini was measured too (0.55 s) and left out: it was the only model that got
# a plain recall question wrong, answering what it was rather than what it had just been told.
CHAT_CHOICES = [
    {"id": "qwen3.5-9b",       "label": "Qwen3.5 9B",     "note": "best answers, ~0.9 s",
     "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"id": "qwen3-vl-4b",      "label": "Qwen3 VL 4B",    "note": "fastest, ~0.5 s",   "extra": {}},
    {"id": "qwen3.5-4b",       "label": "Qwen3.5 4B",     "note": "~0.6 s",
     "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"id": "gemma4-e4b-mlx",   "label": "Gemma 4 E4B",    "note": "~0.6 s, terse",     "extra": {}},
    {"id": "gpt-oss-20b-mlx",  "label": "GPT-OSS 20B",    "note": "~2.5 s, reasons",
     "extra": {"reasoning_effort": "low"}},
    {"id": "Qwen3.8-27B-4bit", "label": "Qwen3.8 27B",    "note": "~3 s, heavyweight",
     "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
]
CHAT_BY_ID = {c["id"]: c for c in CHAT_CHOICES}
TTS_MODEL = os.environ.get("TTS_MODEL", "kokoro-tts")
DAILY_BUDGET = int(os.environ.get("DAILY_BUDGET", "2000"))
ENABLED = os.environ.get("DEMO_ENABLED", "1") != "0"

MAX_AUDIO_BYTES = 4 * 1024 * 1024   # ~2 minutes of 16 kHz mono WAV
MAX_TEXT = 400
PER_IP = (20, 300)                   # 20 requests per 5 minutes per address

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


def voice_catalogue() -> list[dict]:
    """Grouped for a picker: language from the first letter, gender from the second."""
    out = []
    for v in VOICES:
        lang, gender = VOICE_LANGS.get(v[0], "Other"), "female" if v[1] == "f" else "male"
        out.append({"id": v, "label": v.split("_", 1)[1].title(), "language": lang, "gender": gender})
    return out


_hits: dict[str, deque] = defaultdict(deque)
_day = ["", 0]

app = FastAPI(title="voice demo", docs_url=None, redoc_url=None)
STATIC = Path(__file__).parent / "static"


def guard(request: Request) -> None:
    """Cheap protection for a public page pointed at someone's GPU."""
    if not ENABLED:
        raise HTTPException(503, "The demo is switched off right now.")
    if not BASE or not KEY:
        raise HTTPException(500, "The demo is not configured with an inference endpoint.")
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _day[0] != today:
        _day[0], _day[1] = today, 0
    if _day[1] >= DAILY_BUDGET:
        raise HTTPException(429, "The demo has used its budget for today. It resets at midnight UTC.")
    _day[1] += 1

    ip = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
          or (request.client.host if request.client else "?")).strip()
    now, (limit, window) = time.time(), PER_IP
    q = _hits[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "That is a lot of requests. Give it a minute.")
    q.append(now)


async def upstream(method: str, path: str, **kw) -> httpx.Response:
    headers = {"Authorization": f"Bearer {KEY}", "User-Agent": "voice-demo/1.0"}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.request(method, f"{BASE}{path}", headers=headers, **kw)
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True, "enabled": ENABLED, "configured": bool(BASE and KEY),
                         "served_today": _day[1], "budget": DAILY_BUDGET,
                         "models": {"stt": STT_MODEL, "chat": CHAT_MODEL, "tts": TTS_MODEL}})


@app.get("/api/models")
def models() -> JSONResponse:
    default = CHAT_MODEL if CHAT_MODEL in CHAT_BY_ID else CHAT_CHOICES[0]["id"]
    return JSONResponse({"default": default,
                         "models": [{k: c[k] for k in ("id", "label", "note")} for c in CHAT_CHOICES]})


@app.get("/api/voices")
def voices() -> JSONResponse:
    return JSONResponse({"default": DEFAULT_VOICE, "voices": voice_catalogue()})


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
    text = (r.json().get("text") or "").strip()
    return JSONResponse({"text": text, "seconds": round(took, 2), "audio_seconds": round(seconds, 2),
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
    history = []
    for turn in (payload.get("history") or [])[-6:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = str(turn.get("content") or "").strip()[:MAX_TEXT]
        if content:
            history.append({"role": role, "content": content})
    choice = CHAT_BY_ID.get(payload.get("model") or "") or CHAT_BY_ID.get(CHAT_MODEL) or CHAT_CHOICES[0]
    t0 = time.time()
    r = await upstream("POST", "/chat/completions", json={
        "model": choice["id"], "max_tokens": 220, "temperature": 0.4, **choice["extra"],
        "messages": [
            {"role": "system", "content":
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
                "If asked what you are, say you are an open-weight model running locally."},
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
