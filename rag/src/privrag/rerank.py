"""Reranking. Default: pointwise LLM relevance score via the chat model (works with any backend).
Swap in a cross-encoder (bge-reranker via an OpenAI-compatible /rerank endpoint) by setting RERANK_MODEL."""
from __future__ import annotations
import httpx
from .config import settings

_PROMPT = ("Rate how well the PASSAGE answers the QUERY on a 0-10 scale. Reply with only the number.\n\n"
           "QUERY: {q}\n\nPASSAGE: {p} /no_think")

def rerank(q: str, hits: list[dict]) -> list[dict]:
    with httpx.Client(timeout=120) as c:
        for h in hits:
            r = c.post(f"{settings.base_url}/chat/completions",
                       headers={"Authorization": f"Bearer {settings.api_key}"},
                       json={"model": settings.chat_model, "temperature": 0, "max_tokens": 4,
                             "messages": [{"role": "user", "content": _PROMPT.format(q=q, p=h["text"][:1500])}]})
            try:
                h["rerank"] = float(r.json()["choices"][0]["message"]["content"].strip().split()[0])
            except Exception:
                h["rerank"] = 0.0
    return sorted(hits, key=lambda h: -h["rerank"])
