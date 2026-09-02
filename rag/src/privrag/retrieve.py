"""Hybrid retrieval: BM25 + vector, fused by reciprocal rank fusion, ACL-filtered, optional rerank."""
from __future__ import annotations
import re
import lancedb
from rank_bm25 import BM25Okapi
from .config import settings
from .embed import embed
from .ingest import TABLE

def _tok(s: str) -> list[str]: return re.findall(r"[a-z0-9]+", s.lower())

def _rrf(lists: list[list[str]], k: int = 60) -> dict[str, float]:
    s: dict[str, float] = {}
    for ranks in lists:
        for i, rid in enumerate(ranks):
            s[rid] = s.get(rid, 0.0) + 1.0 / (k + i + 1)
    return s

class Retriever:
    def __init__(self) -> None:
        self.db = lancedb.connect(str(settings.index_dir))
        self.t = self.db.open_table(TABLE)
        rows = self.t.to_arrow().to_pylist()
        self.by_id = {r["id"]: r for r in rows}
        self.ids = [r["id"] for r in rows]
        self.bm25 = BM25Okapi([_tok(r["text"]) for r in rows]) if rows else None

    def search(self, q: str, k: int = 8, user_acls: set[str] | None = None, rerank: bool = False) -> list[dict]:
        qv = embed([q])[0]
        vec = [h["id"] for h in self.t.search(qv).limit(k * 4).to_list()]
        bm: list[str] = []
        if self.bm25 is not None:
            sc = self.bm25.get_scores(_tok(q))
            bm = [self.ids[i] for i in sorted(range(len(sc)), key=lambda i: -sc[i])[: k * 4] if sc[i] > 0]
        fused = _rrf([vec, bm])
        allowed = lambda r: user_acls is None or r["acl"] == "*" or r["acl"] in user_acls
        hits = [dict(self.by_id[i], score=fused[i]) for i in sorted(fused, key=lambda i: -fused[i]) if allowed(self.by_id[i])]
        hits = hits[: k * 2] if rerank else hits[:k]
        if rerank:
            from .rerank import rerank as _rr
            hits = _rr(q, hits)[:k]
        for h in hits: h.pop("vector", None)
        return hits
