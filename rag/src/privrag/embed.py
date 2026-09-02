from __future__ import annotations
import httpx
from .config import settings

def embed(texts: list[str], batch: int = 64) -> list[list[float]]:
    out: list[list[float]] = []
    with httpx.Client(timeout=300) as c:
        for i in range(0, len(texts), batch):
            r = c.post(f"{settings.base_url}/embeddings",
                       headers={"Authorization": f"Bearer {settings.api_key}"},
                       json={"model": settings.embed_model, "input": texts[i:i + batch]})
            r.raise_for_status()
            out.extend(d["embedding"] for d in r.json()["data"])
    return out
