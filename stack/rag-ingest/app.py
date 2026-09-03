"""
rag-ingest: minimal, auditable private-docs retrieval service.

- Watches INBOX_DIR for PDFs/TXT/MD, chunks, embeds via LiteLLM, stores in LanceDB.
- Hybrid search: BM25 + vector, fused with reciprocal rank fusion.
- Every query is appended to AUDIT_LOG with who/when/what-was-returned.
- Exposes an OpenAPI spec so Open WebUI can register it as a tool server.

This is the deployable *lite* service. The full reference build with reranking
and an eval harness lives in ../../rag/.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
import lancedb
import pyarrow as pa
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from pypdf import PdfReader
from rank_bm25 import BM25Okapi
from watchfiles import awatch

# ---------------------------------------------------------------- config
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000/v1")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "local/embed")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "local/chat")
INBOX_DIR = Path(os.environ.get("INBOX_DIR", "/data/inbox"))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", "/data/index"))
AUDIT_LOG = Path(os.environ.get("AUDIT_LOG", "/data/audit.jsonl"))
RECTO_URL = os.environ.get("RECTO_URL", "").strip()
CHUNK_TOKENS = int(os.environ.get("CHUNK_TOKENS", "400"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "60"))
# Polling is the default: inotify does not see host-side writes through Docker Desktop bind mounts on macOS.
WATCH_POLL = os.environ.get("WATCH_POLL", "1") == "1"
TABLE = "chunks"

for d in (INBOX_DIR, INDEX_DIR, AUDIT_LOG.parent):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="rag-ingest",
    version="0.1.0",
    description="Private-docs retrieval with citations. Register /openapi.json as a tool server in Open WebUI.",
)
_db = lancedb.connect(str(INDEX_DIR))
_bm25: BM25Okapi | None = None
_bm25_ids: list[str] = []
_src_chunks: dict[str, list[str]] = {}
_doc_ids: set[str] = set()  # content hashes already indexed  # lowercased source filename -> chunk ids (page order)
_lock = asyncio.Lock()


# ---------------------------------------------------------------- helpers
def _audit(event: str, **kw: Any) -> None:
    rec = {"ts": time.time(), "event": event, **kw}
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i : i + size]))
        i += max(1, size - overlap)
    return [c for c in out if c.strip()]


async def _embed(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{LITELLM_BASE_URL}/embeddings",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={"model": EMBED_MODEL, "input": texts},
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]


async def _extract_pages(path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text)]. Uses Recto OCR for scanned PDFs when configured."""
    if path.suffix.lower() in {".txt", ".md"}:
        return [(1, path.read_text(errors="ignore"))]
    if path.suffix.lower() != ".pdf":
        return []
    reader = PdfReader(str(path))
    pages = [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages)]
    if RECTO_URL and sum(len(t) for _, t in pages) < 50 * len(pages):  # looks scanned
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(RECTO_URL, files={"file": (path.name, path.read_bytes())})
            r.raise_for_status()
            data = r.json()
            pages = [(int(p.get("page", i + 1)), p.get("text", "")) for i, p in enumerate(data.get("pages", []))]
    return pages


def _table():
    try:
        return _db.open_table(TABLE)
    except Exception:
        return None


def _rebuild_bm25() -> None:
    global _bm25, _bm25_ids
    t = _table()
    if t is None or t.count_rows() == 0:
        _bm25, _bm25_ids = None, []
        return
    rows = t.to_arrow().to_pylist()
    _bm25_ids = [r["id"] for r in rows]
    _bm25 = BM25Okapi([_tok(r["text"]) for r in rows])
    _src_chunks.clear()
    _doc_ids.clear()
    _doc_ids.update(r["doc_id"] for r in rows)
    for r in sorted(rows, key=lambda r: (r["source"], r["page"], r["id"])):
        _src_chunks.setdefault(r["source"].lower(), []).append(r["id"])


def _id_hits(q: str, per_doc: int = 2) -> list[str]:
    """Exact-identifier lookup: tokens like '26-29237' or 'INV-1042' that appear in a source filename.
    BM25 splits them on punctuation and vectors barely encode them, so match the filename directly."""
    out: list[str] = []
    for tok in re.findall(r"[a-z0-9][a-z0-9\-]{3,}", q.lower()):
        if not any(ch.isdigit() for ch in tok):
            continue
        for src, ids in _src_chunks.items():
            if tok in src:
                out.extend(i for i in ids[:per_doc] if i not in out)
    return out


def _maintain() -> None:
    """Compact small fragments left by per-file appends and drop stale versions."""
    t = _table()
    if t is None:
        return
    try:
        t.compact_files()
        t.cleanup_old_versions()
    except Exception as e:  # maintenance is best-effort
        _audit("maintain_error", error=str(e)[:200])


async def ingest_file(path: Path, rebuild: bool = True, force: bool = False) -> int:
    """Index one file. Pass rebuild=False when ingesting a batch, then call _rebuild_bm25() once.
    Returns -1 when the file's content hash is already indexed (skipped) unless force."""
    doc_id = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    if doc_id in _doc_ids and not force:
        return -1
    pages = await _extract_pages(path)
    recs: list[dict] = []
    for pno, text in pages:
        for j, ch in enumerate(_chunk(text, CHUNK_TOKENS, CHUNK_OVERLAP)):
            recs.append({"id": f"{doc_id}:{pno}:{j}", "doc_id": doc_id, "source": path.name, "page": pno, "text": ch})
    if not recs:
        return 0
    vecs = await _embed([r["text"] for r in recs])
    for r, v in zip(recs, vecs):
        r["vector"] = v
    async with _lock:
        t = _table()
        if t is None:
            schema = pa.schema([
                pa.field("id", pa.string()), pa.field("doc_id", pa.string()), pa.field("source", pa.string()),
                pa.field("page", pa.int32()), pa.field("text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), len(vecs[0]))),
            ])
            t = _db.create_table(TABLE, schema=schema)
        else:
            t.delete(f"doc_id = '{doc_id}'")
        t.add(recs)
        _doc_ids.add(doc_id)
        if rebuild:
            _rebuild_bm25()
    _audit("ingest", source=path.name, doc_id=doc_id, chunks=len(recs))
    return len(recs)


def _rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for i, rid in enumerate(ranks):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + i + 1)
    return scores


async def hybrid_search(q: str, k: int) -> list[dict]:
    t = _table()
    if t is None:
        return []
    qv = (await _embed([q]))[0]
    vec_hits = t.search(qv).limit(k * 3).to_list()
    vec_ranks = [h["id"] for h in vec_hits]
    bm_ranks: list[str] = []
    if _bm25 is not None:
        scores = _bm25.get_scores(_tok(q))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[: k * 3]
        bm_ranks = [_bm25_ids[i] for i in order if scores[i] > 0]
    fused = _rrf([vec_ranks, bm_ranks])
    for n, i in enumerate(_id_hits(q)):  # filename identifier matches outrank everything
        fused[i] = 10.0 - n * 0.001
    by_id = {h["id"]: h for h in vec_hits}
    missing = [i for i in fused if i not in by_id]
    if missing:
        rows = t.search().where(f"id IN ({','.join(repr(m) for m in missing)})").limit(len(missing)).to_list()
        by_id.update({r["id"]: r for r in rows})
    top = sorted(fused, key=lambda i: -fused[i])[:k]
    return [{"id": i, "source": by_id[i]["source"], "page": by_id[i]["page"], "text": by_id[i]["text"], "score": round(fused[i], 5)} for i in top if i in by_id]


# ---------------------------------------------------------------- API
class QueryIn(BaseModel):
    question: str
    k: int = 6
    answer: bool = True


@app.get("/health")
def health():
    t = _table()
    return {"ok": True, "chunks": t.count_rows() if t else 0, "embed_model": EMBED_MODEL}


@app.post("/ingest", summary="Ingest new/changed files in the inbox (force=true re-embeds everything)")
async def ingest_all(force: bool = False):
    n = skipped = done = 0
    for p in sorted(INBOX_DIR.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            r = await ingest_file(p, rebuild=False, force=force)
            if r < 0:
                skipped += 1
            else:
                n += r
                done += 1
    if done:
        async with _lock:
            _maintain()
            _rebuild_bm25()
        _audit("batch", files=done, chunks=n)
    return {"chunks_indexed": n, "files_indexed": done, "files_skipped": skipped}


@app.get("/search", summary="Hybrid search over private documents; returns chunks with source and page")
async def search(q: str = Query(..., description="natural-language query"), k: int = 6, x_user: str | None = Header(default=None)):
    hits = await hybrid_search(q, k)
    _audit("search", user=x_user, q_hash=hashlib.sha256(q.encode()).hexdigest()[:16], returned=[h["id"] for h in hits])
    return {"hits": hits}


@app.post("/query", summary="Answer a question from private documents with citations")
async def query(body: QueryIn, x_user: str | None = Header(default=None)):
    hits = await hybrid_search(body.question, body.k)
    _audit("query", user=x_user, q_hash=hashlib.sha256(body.question.encode()).hexdigest()[:16], returned=[h["id"] for h in hits])
    if not body.answer or not hits:
        return {"answer": None, "citations": hits}
    ctx = "\n\n".join(f"[{i+1}] ({h['source']} p.{h['page']}) {h['text']}" for i, h in enumerate(hits))
    prompt = (
        "Answer ONLY from the numbered sources below. Cite as [n] after each claim and name the source document "
        "(its filename) the first time you use it. "
        "If the sources do not contain the answer, say exactly: 'Not found in the provided documents.'\n\n"
        f"SOURCES:\n{ctx}\n\nQUESTION: {body.question}"
    )
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(
            f"{LITELLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={"model": CHAT_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
        )
        if r.status_code != 200:
            raise HTTPException(502, f"chat backend: {r.text[:200]}")
        ans = r.json()["choices"][0]["message"]["content"]
    return {"answer": ans, "citations": hits}


@app.on_event("startup")
async def _startup():
    _rebuild_bm25()

    async def watch():
        async for changes in awatch(INBOX_DIR, force_polling=WATCH_POLL, poll_delay_ms=2000):
            paths = sorted({Path(p) for _, p in changes})
            done = 0
            for path in paths:
                if path.is_file() and not path.name.startswith("."):
                    try:
                        if await ingest_file(path, rebuild=False) >= 0:
                            done += 1
                    except Exception as e:  # keep watching
                        _audit("ingest_error", source=path.name, error=str(e)[:200])
            if done:
                async with _lock:
                    _maintain()
                    _rebuild_bm25()
                _audit("batch", files=done)

    asyncio.create_task(watch())
