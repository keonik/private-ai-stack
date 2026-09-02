from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    base_url: str = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
    api_key: str = os.environ.get("LITELLM_API_KEY", "")
    embed_model: str = os.environ.get("EMBED_MODEL", "local/embed")
    chat_model: str = os.environ.get("CHAT_MODEL", "local/chat")
    rerank_model: str = os.environ.get("RERANK_MODEL", "")   # empty = LLM pointwise rerank via chat_model
    index_dir: Path = Path(os.environ.get("INDEX_DIR", "./index"))
    chunk_tokens: int = int(os.environ.get("CHUNK_TOKENS", "400"))
    chunk_overlap: int = int(os.environ.get("CHUNK_OVERLAP", "60"))

settings = Settings()
