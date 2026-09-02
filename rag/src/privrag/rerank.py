"""Reranking. Default: pointwise LLM relevance score via the chat model (works with any backend).
Swap in a cross-encoder (bge-reranker via an OpenAI-compatible /rerank endpoint) by setting RERANK_MODEL.

Sends both think=false (Ollama) and chat_template_kwargs.enable_thinking=false (oMLX / vLLM / mlx-lm),
because a /no_think prompt suffix is honored by neither route through LiteLLM. Still
reads `content` or `reasoning_content` and takes the first number found, for backends that ignore the flag.
A passage that yields no parsable score gets -1 so failures are visible, not silently tied at 0."""
from __future__ import annotations
import re
import httpx
from .config import settings

_PROMPT = ("Rate how well the PASSAGE answers the QUERY on a 0-10 scale. Reply with only the number.\n\n"
           "QUERY: {q}\n\nPASSAGE: {p}")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

def _score(text: str) -> float:
    m = _NUM.search(text or "")
    return float(m.group()) if m else -1.0

def rerank(q: str, hits: list[dict]) -> list[dict]:
    with httpx.Client(timeout=120) as c:
        for h in hits:
            r = c.post(f"{settings.base_url}/chat/completions",
                       headers={"Authorization": f"Bearer {settings.api_key}"},
                       # Disable reasoning on every route: `think` is what Ollama honors, `chat_template_kwargs` is what
                       # OpenAI-compatible MLX/vLLM servers honor. Each backend ignores the other's flag.
                       json={"model": settings.chat_model, "temperature": 0, "max_tokens": 16, "think": False,
                             "chat_template_kwargs": {"enable_thinking": False},
                             "messages": [{"role": "user", "content": _PROMPT.format(q=q, p=h["text"][:1500])}]})
            try:
                m = r.json()["choices"][0]["message"]
                h["rerank"] = _score((m.get("content") or "") + " " + (m.get("reasoning_content") or ""))
            except Exception:
                h["rerank"] = -1.0
    return sorted(hits, key=lambda h: -h["rerank"])
