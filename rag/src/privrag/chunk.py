"""Chunking: word-window with overlap, page-aware. Swap for a semantic chunker later; keep the interface."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Chunk:
    id: str
    doc_id: str
    source: str
    page: int
    text: str
    acl: str = "*"   # chunk-level access tag; filtered at query time (see phi-pattern.md)

def window(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    out, i = [], 0
    step = max(1, size - overlap)
    while i < len(words):
        out.append(" ".join(words[i:i + size])); i += step
    return [c for c in out if c.strip()]

def chunk_pages(doc_id: str, source: str, pages: list[tuple[int, str]], size: int, overlap: int, acl: str = "*") -> list[Chunk]:
    out: list[Chunk] = []
    for pno, text in pages:
        for j, c in enumerate(window(text, size, overlap)):
            out.append(Chunk(id=f"{doc_id}:{pno}:{j}", doc_id=doc_id, source=source, page=pno, text=c, acl=acl))
    return out
