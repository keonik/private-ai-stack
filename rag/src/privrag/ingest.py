from __future__ import annotations
import hashlib
from pathlib import Path
import lancedb, pyarrow as pa
from pypdf import PdfReader
from .chunk import chunk_pages
from .config import settings
from .embed import embed

TABLE = "chunks"

def read_pages(path: Path) -> list[tuple[int, str]]:
    if path.suffix.lower() in {".txt", ".md"}:
        return [(1, path.read_text(errors="ignore"))]
    if path.suffix.lower() == ".pdf":
        return [(i + 1, (p.extract_text() or "")) for i, p in enumerate(PdfReader(str(path)).pages)]
    return []

def ingest_paths(paths: list[Path], acl: str = "*") -> int:
    db = lancedb.connect(str(settings.index_dir))
    total = 0
    for p in paths:
        doc_id = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        chunks = chunk_pages(doc_id, p.name, read_pages(p), settings.chunk_tokens, settings.chunk_overlap, acl)
        if not chunks:
            continue
        vecs = embed([c.text for c in chunks])
        rows = [{**c.__dict__, "vector": v} for c, v in zip(chunks, vecs)]
        try:
            t = db.open_table(TABLE); t.delete(f"doc_id = '{doc_id}'"); t.add(rows)
        except Exception:
            schema = pa.schema([pa.field("id", pa.string()), pa.field("doc_id", pa.string()), pa.field("source", pa.string()),
                                pa.field("page", pa.int32()), pa.field("text", pa.string()), pa.field("acl", pa.string()),
                                pa.field("vector", pa.list_(pa.float32(), len(vecs[0])))])
            db.create_table(TABLE, schema=schema).add(rows)
        total += len(rows)
    return total
