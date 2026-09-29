"""laya: typed decisions in one forward pass, on this machine.

A decision with a known answer set — which queue, is this urgent, which of six intents — does not need a
language model writing sentences. Laya (Apache-2.0) is an encoder that scores every allowed answer in one
pass and returns a probability for each. Roughly 30 ms a decision here against a second or more for a chat
model, with no prompt to maintain and no output to parse.

    POST /decide   {"state": "...", "questions": {"intent": {...}}}  ->  answers with probabilities
    GET  /health   what is loaded, and whether calibration was fitted here
    GET  /metrics  Prometheus

Calibration is the whole reason this is a service and not a library call. Laya's own loader warns that the
shipped checkpoint "ships temperatures outside [0.5, 5] ... treat confidence from the affected buckets as
uncalibrated", and measured here it is: 94.5% accurate on ag_news with an expected calibration error of
0.195, meaning a "90%" answer is nothing of the sort. `calibrate.py` fits one temperature per (question
type, option count) on your own labelled rows and writes calibration.json; this service applies it and says
so in /health. Fitted, the same model's error drops into the same range as anything else here.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from starlette.responses import Response

MODEL = os.environ.get("LAYA_MODEL", "convaiinnovations/laya")
SUBFOLDER = os.environ.get("LAYA_SUBFOLDER") or None
CALIBRATION = Path(os.environ.get("LAYA_CALIBRATION", Path(__file__).parent / "calibration.json"))
KEY_FILE = Path(os.environ.get("LAYA_KEY_FILE", "~/.config/laya/api.key")).expanduser()
MAX_STATE = int(os.environ.get("LAYA_MAX_STATE", "20000"))

DECISIONS = Counter("laya_decisions_total", "Questions answered", ["type", "calibrated"])
ERRORS = Counter("laya_errors_total", "Requests refused or failed", ["reason"])
LATENCY = Histogram("laya_decision_seconds", "Seconds per request", buckets=(.01, .02, .05, .1, .25, .5, 1, 2))

app = FastAPI(title="laya", docs_url="/docs", redoc_url=None)
_agent: Any = None
_calibration: dict = {}


def api_key() -> str:
    """From the environment, else a 600 file. Never from the plist: launchd plists are world-readable."""
    return os.environ.get("LAYA_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.is_file() else "")


def auth(authorization: str = Header("")) -> None:
    import hmac

    key = api_key()
    if not key:
        return  # unset means "bound to localhost, no auth" — the same rule the other services here use
    offered = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(offered.encode(), key.encode()):
        ERRORS.labels("unauthorised").inc()
        raise HTTPException(401, "Bad or missing bearer token.")


def agent():
    global _agent, _calibration
    if _agent is None:
        import laya_mlx

        _agent = laya_mlx.Agent(MODEL, subfolder=SUBFOLDER)
        if CALIBRATION.is_file():
            _calibration = json.loads(CALIBRATION.read_text()).get("temperatures", {})
    return _agent


def bucket(question: dict) -> str:
    """Calibration is per (type, option count): a six-way choice is not as confident as a two-way one."""
    kind = question.get("type", "choice")
    n = len(question.get("criteria") or []) if kind != "bool" else 2
    return f"{kind}:{n}"


def recalibrate(probabilities: dict[str, float], temperature: float) -> dict[str, float]:
    """Temperature scaling on probabilities: back to logits, divide, softmax again."""
    import math

    logits = {k: math.log(max(p, 1e-9)) / temperature for k, p in probabilities.items()}
    top = max(logits.values())
    exp = {k: math.exp(v - top) for k, v in logits.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}


class Ask(BaseModel):
    state: str
    questions: dict[str, dict]


@app.post("/decide", dependencies=[Depends(auth)])
def decide(ask: Ask) -> dict:
    if not ask.state.strip():
        ERRORS.labels("empty_state").inc()
        raise HTTPException(400, "state is empty.")
    if len(ask.state) > MAX_STATE:
        ERRORS.labels("state_too_long").inc()
        raise HTTPException(413, f"state is longer than {MAX_STATE} characters.")
    if not ask.questions:
        ERRORS.labels("no_questions").inc()
        raise HTTPException(400, "questions is empty.")

    t0 = time.time()
    try:
        # laya-mlx knows choice and score, not bool. Ask a bool as a two-option choice and map it back, so
        # callers can use the same question shapes the other typed-decision service here accepts.
        asked, bools = {}, set()
        for name, q in ask.questions.items():
            if q.get("type") == "bool":
                bools.add(name)
                asked[name] = {"type": "choice", "instructions": q["instructions"],
                               "criteria": {"yes": "the statement is true", "no": "the statement is false"}}
            else:
                asked[name] = q
        raw = agent().predict(ask.state, asked)["answers"]
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — a bad question should not take the service down
        ERRORS.labels("model").inc()
        raise HTTPException(400, f"laya could not answer that: {e}") from e

    out = {}
    for name, answer in raw.items():
        probs = {k: float(v) for k, v in (answer.get("probabilities") or {}).items()}
        temp = _calibration.get(bucket(ask.questions[name]))
        if temp and probs:
            probs = recalibrate(probs, float(temp))
        if name in bools:
            probs = {"true": probs.get("yes", 0.0), "false": probs.get("no", 0.0)}
        choice = max(probs, key=probs.get) if probs else answer.get("choice")
        out[name] = {"type": ask.questions[name].get("type", "choice"), "choice": choice,
                     "confidence": round(float(probs.get(choice, answer.get("confidence", 0.0))), 4),
                     "probabilities": {k: round(v, 4) for k, v in probs.items()},
                     "calibrated": bool(temp)}
        DECISIONS.labels(out[name]["type"], str(bool(temp)).lower()).inc()
    LATENCY.observe(time.time() - t0)
    return {"model": MODEL + (f"/{SUBFOLDER}" if SUBFOLDER else ""), "answers": out,
            "seconds": round(time.time() - t0, 4)}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": MODEL, "subfolder": SUBFOLDER, "loaded": _agent is not None,
            "calibrated_buckets": sorted(_calibration), "auth": "bearer" if api_key() else "none",
            "calibration_file": str(CALIBRATION) if CALIBRATION.is_file() else None}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.on_event("startup")
def warm() -> None:
    import threading

    threading.Thread(target=agent, daemon=True).start()  # ~2 s of model load, off the first request
