from __future__ import annotations
import httpx
from .config import settings

NOT_FOUND = "Not found in the provided documents."

def answer(q: str, hits: list[dict]) -> str:
    if not hits: return NOT_FOUND
    ctx = "\n\n".join(f"[{i+1}] ({h['source']} p.{h['page']}) {h['text']}" for i, h in enumerate(hits))
    prompt = ("Answer ONLY from the numbered sources. Cite as [n] after each claim. "
              f"If the sources do not contain the answer, reply exactly: '{NOT_FOUND}'\n\nSOURCES:\n{ctx}\n\nQUESTION: {q} /no_think")
    with httpx.Client(timeout=300) as c:
        r = c.post(f"{settings.base_url}/chat/completions", headers={"Authorization": f"Bearer {settings.api_key}"},
                   json={"model": settings.chat_model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
