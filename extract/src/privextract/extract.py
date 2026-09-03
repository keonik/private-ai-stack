"""LLM structured extraction via LiteLLM with JSON-schema-constrained output, then validation rules."""
from __future__ import annotations
import json, os
import httpx
from pydantic import BaseModel
from .schemas import SCHEMAS

BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
KEY = os.environ.get("LITELLM_API_KEY", "")
MODEL = os.environ.get("CHAT_MODEL", "local/chat")
REVIEW_THRESHOLD = float(os.environ.get("REVIEW_THRESHOLD", "0.8"))
# Reasoning ("thinking") off by default: schema-constrained extraction does not benefit and it is 5-10x slower.
# Set EXTRACT_THINK=1 to let the model reason first (useful on messy scans). Both flags are sent because
# Ollama honors `think` and OpenAI-compatible MLX/vLLM servers honor `chat_template_kwargs`.
THINK = os.environ.get("EXTRACT_THINK", "0").lower() in ("1", "true", "yes")

_SYS = ("You extract fields from documents into JSON matching the given schema. For every field give value, "
        "confidence (0-1, honest), and evidence (the exact source text). Use null when absent. Dates ISO-8601. "
        "Numbers as numbers, no currency symbols. Boolean fields must be true or false, never null: a clear absence "
        "is false with high confidence. Output only JSON.")

def extract(doc_text: str, kind: str) -> BaseModel:
    schema_cls = SCHEMAS[kind]
    schema = schema_cls.model_json_schema()
    # Make every key of the F wrapper required. With `value` optional in the schema, constrained decoders let
    # small models emit {"confidence":..,"evidence":..} and silently drop the value.
    for d in schema.get("$defs", {}).values():
        if isinstance(d, dict) and "properties" in d:
            d["required"] = list(d["properties"])
    with httpx.Client(timeout=300) as c:
        r = c.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {KEY}"},
                   json={"model": MODEL, "temperature": 0, "think": THINK,
                         "chat_template_kwargs": {"enable_thinking": THINK},
                         "response_format": {"type": "json_schema", "json_schema": {"name": kind, "schema": schema}},
                         "messages": [{"role": "system", "content": _SYS},
                                      {"role": "user", "content": f"SCHEMA:\n{json.dumps(schema)}\n\nDOCUMENT:\n{doc_text[:24000]}"}]})
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
    return schema_cls.model_validate_json(raw)

def validate(obj: BaseModel) -> list[str]:
    """Cross-field rules that catch hallucinated numbers. Returns human-readable problems."""
    issues: list[str] = []
    d = obj.model_dump()
    if "total" in d and d.get("subtotal", {}).get("value") is not None and d.get("tax", {}).get("value") is not None and d["total"]["value"] is not None:
        if abs((d["subtotal"]["value"] + d["tax"]["value"]) - d["total"]["value"]) > 0.02:
            issues.append("subtotal + tax != total")
    if "line_items" in d and d["line_items"] and d.get("subtotal", {}).get("value") is not None:
        s = sum((li["amount"]["value"] or 0) for li in d["line_items"])
        if abs(s - d["subtotal"]["value"]) > 0.02: issues.append("line items do not sum to subtotal")
    if "billing_period_start" in d and d["billing_period_start"]["value"] and d["billing_period_end"]["value"]:
        if d["billing_period_end"]["value"] < d["billing_period_start"]["value"]: issues.append("billing period end before start")
    return issues

def needs_review(obj: BaseModel, issues: list[str]) -> list[str]:
    low = [k for k, v in obj.model_dump().items() if isinstance(v, dict) and "confidence" in v and v["confidence"] < REVIEW_THRESHOLD]
    return low + [f"rule: {i}" for i in issues]
