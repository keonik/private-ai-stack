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
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import httpx
import lancedb
import pyarrow as pa
from fastapi import Depends, FastAPI, Header, HTTPException, Query
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
_doc_ids: set[str] = set()  # content hashes already indexed
_bm25_src: list[str] = []   # source per BM25 row, for filtered search
META_SUFFIX = ".meta.json"  # sidecar written by extract/scripts/batch.py: {"source", "fields": {...}, "review": [...]}
DOC_SUFFIXES = {".pdf", ".txt", ".md"}
_meta: dict[str, dict] = {}  # source filename -> sidecar
_sources: set[str] = set()   # exact source filenames in the index
ACL_FILE = Path(os.environ.get("ACL_FILE", "/data/acl.json"))  # optional per-group document scoping
_acl_cache: tuple[float, dict | None] = (0.0, None)
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
    _bm25_src[:] = [r["source"] for r in rows]
    _bm25 = BM25Okapi([_tok(r["text"]) for r in rows])
    _src_chunks.clear()
    _sources.clear()
    _sources.update(r["source"] for r in rows)
    _doc_ids.clear()
    _doc_ids.update(r["doc_id"] for r in rows)
    for r in sorted(rows, key=lambda r: (r["source"], r["page"], r["id"])):
        _src_chunks.setdefault(r["source"].lower(), []).append(r["id"])


def _is_doc(p: Path) -> bool:
    return p.is_file() and not p.name.startswith(".") and p.suffix.lower() in DOC_SUFFIXES


def _load_meta() -> int:
    """(Re)load every sidecar in the inbox. Cheap: a few hundred small JSON files."""
    _meta.clear()
    for p in INBOX_DIR.glob(f"*{META_SUFFIX}"):
        try:
            d = json.loads(p.read_text())
            _meta[d.get("source") or p.name[: -len(META_SUFFIX)]] = d
        except Exception as e:
            _audit("meta_error", source=p.name, error=str(e)[:200])
    return len(_meta)


def _match(meta: dict, flt: dict) -> bool:
    """Equality per key; strings match case-insensitively as substrings. Keys look in `fields` first."""
    f = meta.get("fields", {})
    for k, v in flt.items():
        cur = f.get(k) if k in f else meta.get(k)
        if isinstance(v, str) and isinstance(cur, str):
            if v.lower() not in cur.lower():
                return False
        elif cur != v:
            return False
    return True


def _allowed_sources(flt: dict | None) -> set[str] | None:
    if not flt:
        return None
    return {src for src, m in _meta.items() if _match(m, flt)}


def _acl() -> dict | None:
    """ACL file: {"admin_all": true, "default": [globs], "groups": {"group name": [globs]}}.
    Globs match source filenames case-insensitively. No file = no restriction (single-tenant)."""
    global _acl_cache
    try:
        m = ACL_FILE.stat().st_mtime
    except FileNotFoundError:
        return None
    if m != _acl_cache[0]:
        _acl_cache = (m, json.loads(ACL_FILE.read_text()))
    return _acl_cache[1]


class Caller:
    """Who is asking, from headers Open WebUI adds to the tool-server connection
    (Headers: X-User-Id: {{USER_ID}}, X-User-Role: {{USER_ROLE}}, X-User-Groups: {{USER_GROUPS}})."""

    def __init__(self, x_user: str | None = Header(default=None), x_user_id: str | None = Header(default=None),
                 x_user_role: str | None = Header(default=None), x_user_groups: str | None = Header(default=None),
                 x_openwebui_user_id: str | None = Header(default=None), x_openwebui_user_role: str | None = Header(default=None)):
        self.user = x_user_id or x_openwebui_user_id or x_user
        self.role = x_user_role or x_openwebui_user_role
        self.groups = [g.strip() for g in (x_user_groups or "").split(",") if g.strip()]
        self.scope = self._scope()

    def _scope(self) -> set[str] | None:
        """Exact source names this caller may see, or None for unrestricted."""
        acl = _acl()
        if acl is None or (self.role == "admin" and acl.get("admin_all", True)):
            return None
        pats = list(acl.get("default", []))
        for g in self.groups:
            pats += acl.get("groups", {}).get(g, [])
        pats = [p.lower() for p in pats]
        return {src for src in _sources if any(fnmatch(src.lower(), p) for p in pats)}


def _restrict(allowed: set[str] | None, scope: set[str] | None) -> set[str] | None:
    if scope is None:
        return allowed
    return scope if allowed is None else allowed & scope


def _parse_filter(s: str | None) -> dict:
    if not s:
        return {}
    try:
        d = json.loads(s)
    except Exception:
        raise HTTPException(400, "filter must be a JSON object, e.g. {\"animal_involved\": true}")
    if not isinstance(d, dict):
        raise HTTPException(400, "filter must be a JSON object")
    return d


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


def _q(s: str) -> str:
    return s.replace("'", "''")


def _remove_source(name: str) -> int:
    """Drop every chunk of a source file (caller holds _lock). Returns chunks removed."""
    t = _table()
    if t is None:
        return 0
    ids = _src_chunks.get(name.lower(), [])
    if not ids:
        return 0
    t.delete(f"source = '{_q(name)}'")
    _meta.pop(name, None)
    return len(ids)


def _sweep_missing() -> int:
    """Remove indexed sources whose file is no longer in the inbox (caller holds _lock)."""
    present = {p.name.lower() for p in INBOX_DIR.iterdir() if _is_doc(p)}
    gone = [src for src in list(_src_chunks) if src not in present]
    n = 0
    for src in gone:
        n += _remove_source(src)
        _audit("remove", source=src, reason="missing")
    return len(gone)


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
            t.delete(f"doc_id = '{doc_id}' OR source = '{_q(path.name)}'")
        t.add(recs)
        _doc_ids.add(doc_id)
        if rebuild:
            _rebuild_bm25()
    _audit("ingest", source=path.name, doc_id=doc_id, chunks=len(recs))
    return len(recs)


def _rrf(rank_lists: list[list[str]], k: int = 60, weights: list[float] | None = None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for li, ranks in enumerate(rank_lists):
        w = weights[li] if weights else 1.0
        for i, rid in enumerate(ranks):
            scores[rid] = scores.get(rid, 0.0) + w / (k + i + 1)
    return scores


BM25_WEIGHT = float(os.environ.get("BM25_WEIGHT", "3.0"))  # lexical weight in the fusion, tuned on form-heavy corpora; see README "Retrieval evaluation"


async def hybrid_search(q: str, k: int, flt: dict | None = None, scope: set[str] | None = None, mode: str = "hybrid",
                        bm25_weight: float | None = None) -> list[dict]:
    """mode: hybrid (RRF of vector + BM25 + identifier hits, the default) | vector | bm25 — the last two exist for evaluation.
    bm25_weight scales the lexical list's contribution in the fusion (vector is 1.0); default BM25_WEIGHT env."""
    t = _table()
    if t is None:
        return []
    allowed = _restrict(_allowed_sources(flt), scope)
    if allowed is not None and not allowed:
        return []
    qv = (await _embed([q]))[0]
    vq = t.search(qv)
    if allowed is not None:
        vq = vq.where("source IN (" + ",".join("'" + a.replace("'", "''") + "'" for a in allowed) + ")", prefilter=True)
    vec_hits = vq.limit(k * 3).to_list()
    vec_ranks = [h["id"] for h in vec_hits]
    bm_ranks: list[str] = []
    if _bm25 is not None:
        scores = _bm25.get_scores(_tok(q))
        cand = range(len(scores)) if allowed is None else [i for i in range(len(scores)) if _bm25_src[i] in allowed]
        order = sorted(cand, key=lambda i: -scores[i])[: k * 3]
        bm_ranks = [_bm25_ids[i] for i in order if scores[i] > 0]
    if mode == "vector":
        fused = {i: 1.0 / (n + 1) for n, i in enumerate(vec_ranks)}
    elif mode == "bm25":
        fused = {i: 1.0 / (n + 1) for n, i in enumerate(bm_ranks)}
    else:
        fused = _rrf([vec_ranks, bm_ranks], weights=[1.0, BM25_WEIGHT if bm25_weight is None else bm25_weight])
        for n, i in enumerate(_id_hits(q)):  # filename identifier matches outrank everything
            src = next((sname for sname, ids in _src_chunks.items() if i in ids), None)
            if allowed is None or (src is not None and any(src == a.lower() for a in allowed)):
                fused[i] = 10.0 - n * 0.001
    by_id = {h["id"]: h for h in vec_hits}
    missing = [i for i in fused if i not in by_id]
    if missing:
        rows = t.search().where(f"id IN ({','.join(repr(m) for m in missing)})").limit(len(missing)).to_list()
        by_id.update({r["id"]: r for r in rows})
    top = sorted(fused, key=lambda i: -fused[i])[:k]
    return [{"id": i, "source": by_id[i]["source"], "page": by_id[i]["page"], "text": by_id[i]["text"], "score": round(fused[i], 5),
             "fields": _meta.get(by_id[i]["source"], {}).get("fields")} for i in top if i in by_id]


# ---------------------------------------------------------------- API
class QueryIn(BaseModel):
    question: str
    k: int = 6
    answer: bool = True
    filter: dict | None = None  # restrict to documents whose extracted fields match, e.g. {"county_code": 67}


@app.get("/health")
def health():
    t = _table()
    return {"ok": True, "chunks": t.count_rows() if t else 0, "docs_with_fields": len(_meta), "embed_model": EMBED_MODEL}


@app.get("/fields", summary="Which extracted fields exist and example values; use before filtering /docs or /search")
def fields(who: Caller = Depends()):
    out: dict[str, dict] = {}
    visible = {src: m for src, m in _meta.items() if who.scope is None or src in who.scope}
    for m in visible.values():
        for k, v in m.get("fields", {}).items():
            e = out.setdefault(k, {"type": type(v).__name__ if v is not None else "null", "docs_with_value": 0, "example_values": []})
            if v is None:
                continue
            e["docs_with_value"] += 1
            if v not in e["example_values"] and len(e["example_values"]) < 6 and not (isinstance(v, str) and len(v) > 80):
                e["example_values"].append(v)
    return {"docs_with_fields": len(visible), "fields": out}


@app.get("/documents", summary="List documents whose extracted fields match a filter, e.g. filter={\"animal_involved\": true}. Answers 'which documents ...' questions exactly, without retrieval")
def docs(filter: str | None = Query(default=None, description="JSON object of field: value; strings match as case-insensitive substrings"),
         limit: int = 50, who: Caller = Depends()):
    flt = _parse_filter(filter)
    hits = [{"source": src, "fields": m.get("fields", {}), "review": m.get("review", []), "model": m.get("model")}
            for src, m in sorted(_meta.items()) if (who.scope is None or src in who.scope) and _match(m, flt)]
    _audit("docs", user=who.user, filter=flt, matched=len(hits))
    return {"matched": len(hits), "docs": hits[:limit]}


@app.post("/ingest", summary="Ingest new/changed files in the inbox (force=true re-embeds everything)")
async def ingest_all(force: bool = False):
    n = skipped = done = 0
    _load_meta()
    for p in sorted(INBOX_DIR.iterdir()):
        if _is_doc(p):
            r = await ingest_file(p, rebuild=False, force=force)
            if r < 0:
                skipped += 1
            else:
                n += r
                done += 1
    async with _lock:
        removed = _sweep_missing()
        if done or removed:
            _maintain()
            _rebuild_bm25()
    if done or removed:
        _audit("batch", files=done, chunks=n, removed=removed)
    return {"chunks_indexed": n, "files_indexed": done, "files_skipped": skipped, "files_removed": removed}


@app.get("/search", summary="Hybrid search over private documents; returns chunks with source and page")
async def search(q: str = Query(..., description="natural-language query"), k: int = 6,
                 filter: str | None = Query(default=None, description="optional JSON object of extracted field: value to restrict documents"),
                 mode: str = Query(default="hybrid", pattern="^(hybrid|vector|bm25)$", description="hybrid (default); vector or bm25 alone for evaluation"),
                 bm25_weight: float | None = Query(default=None, description="evaluation only: lexical weight in the fusion"),
                 who: Caller = Depends()):
    hits = await hybrid_search(q, k, _parse_filter(filter), who.scope, mode, bm25_weight)
    _audit("search", user=who.user, q_hash=hashlib.sha256(q.encode()).hexdigest()[:16], returned=[h["id"] for h in hits])
    return {"hits": hits}


@app.post("/query", summary="Answer a question from private documents with citations")
async def query(body: QueryIn, who: Caller = Depends()):
    hits = await hybrid_search(body.question, body.k, body.filter, who.scope)
    _audit("query", user=who.user, q_hash=hashlib.sha256(body.question.encode()).hexdigest()[:16], returned=[h["id"] for h in hits])
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
    _load_meta()
    if _sweep_missing():  # files removed while we were down
        _maintain()
        _rebuild_bm25()

    async def watch():
        async for changes in awatch(INBOX_DIR, force_polling=WATCH_POLL, poll_delay_ms=2000):
            paths = sorted({Path(p) for _, p in changes})
            done = 0
            if any(p.name.endswith(META_SUFFIX) for p in paths):
                _load_meta()
            removed = 0
            for path in paths:
                if _is_doc(path):
                    try:
                        if await ingest_file(path, rebuild=False) >= 0:
                            done += 1
                    except Exception as e:  # keep watching
                        _audit("ingest_error", source=path.name, error=str(e)[:200])
                elif not path.exists() and path.suffix.lower() in DOC_SUFFIXES and path.name.lower() in _src_chunks:
                    async with _lock:
                        if _remove_source(path.name):
                            removed += 1
                            _audit("remove", source=path.name, reason="deleted")
            if done or removed:
                async with _lock:
                    _maintain()
                    _rebuild_bm25()
                _audit("batch", files=done, removed=removed)

    asyncio.create_task(watch())
