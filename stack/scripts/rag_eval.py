"""Retrieval evaluation: does the search find the right document?

Builds a golden set of natural-language questions from the extracted-field sidecars (one per sampled
document, written by the small chat model from that document's summary, without the report number),
caches it, then measures for each search mode whether the target document appears in the top k.

    python3 scripts/rag_eval.py            # 60 questions, seed 0, modes hybrid/vector/bm25
    python3 scripts/rag_eval.py --n 100 --regen

Needs the stack up. Reads LITELLM_MASTER_KEY from .env. Golden set cached at data/eval/questions.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ENV = {l.split("=", 1)[0]: l.split("=", 1)[1].strip() for l in (HERE / ".env").read_text().splitlines() if "=" in l and not l.startswith("#")}
RAG = os.environ.get("RAG_URL", f"http://localhost:{ENV.get('RAG_PORT', '8088')}")
LLM = os.environ.get("LITELLM_BASE_URL", f"http://localhost:{ENV.get('LITELLM_PORT', '4000')}")
QMODEL = os.environ.get("QUESTION_MODEL", "local/chat-small")


def env_key() -> str:
    return os.environ.get("LITELLM_API_KEY") or ENV.get("LITELLM_MASTER_KEY") or exit("LITELLM_MASTER_KEY not in .env")


def get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def ask(key: str, prompt: str) -> str:
    body = json.dumps({"model": QMODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 80,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(f"{LLM}/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip().strip('"')


PROMPT = """A person is searching a private archive of crash reports. Write ONE short question they might type to find this
specific report. Use concrete details from the summary (road or street names, what happened, vehicles, animals, injuries).
Do NOT include the report number, agency, officer, or county code. Output only the question.

Summary: {summary}
Location: {locality}"""


def build(key: str, n: int, seed: int, out: Path) -> list[dict]:
    docs = get(f"{RAG}/documents?limit=100000", {"X-User-Role": "admin"})["docs"]
    docs = [d for d in docs if d["fields"].get("summary")]
    random.Random(seed).shuffle(docs)
    rows = []
    for d in docs[:n]:
        q = ask(key, PROMPT.format(summary=d["fields"]["summary"], locality=d["fields"].get("locality") or "unknown"))
        rows.append({"source": d["source"], "question": q})
        print(f"  {d['source'][-24:]}  {q}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=10); ap.add_argument("--regen", action="store_true")
    ap.add_argument("--modes", default="hybrid,vector,bm25", help="also hybrid:W to set the lexical weight, e.g. hybrid:2")
    ap.add_argument("--gold", default="data/eval/questions.jsonl")
    a = ap.parse_args()
    gold = HERE / a.gold
    if a.regen or not gold.exists():
        print(f"building {a.n} questions with {QMODEL} ...")
        rows = build(env_key(), a.n, a.seed, gold)
    else:
        rows = [json.loads(l) for l in gold.read_text().splitlines() if l.strip()]
    print(f"\n{len(rows)} questions, k={a.k}\n")
    print("| mode | recall@1 | recall@5 | recall@10 | MRR | median ms |\n|---|---|---|---|---|---|")
    misses: dict[str, list] = {}
    for mode in a.modes.split(","):
        ranks, lat = [], []
        m, _, w = mode.partition(":")
        extra = f"&bm25_weight={w}" if w else ""
        for r in rows:
            t0 = time.time()
            hits = get(f"{RAG}/search?q={urllib.parse.quote(r['question'])}&k={a.k}&mode={m}{extra}", {"X-User-Role": "admin"})["hits"]
            lat.append((time.time() - t0) * 1000)
            srcs = []
            for h in hits:  # rank by document, not chunk
                if h["source"] not in srcs:
                    srcs.append(h["source"])
            rank = srcs.index(r["source"]) + 1 if r["source"] in srcs else None
            ranks.append(rank)
            if rank is None:
                misses.setdefault(mode, []).append(r)
        n = len(ranks)
        rec = lambda kk: sum(1 for x in ranks if x and x <= kk) / n
        mrr = sum(1 / x for x in ranks if x) / n
        print(f"| {mode} | {rec(1):.2f} | {rec(5):.2f} | {rec(a.k):.2f} | {mrr:.2f} | {statistics.median(lat):.0f} |")
    for mode, ms in misses.items():
        print(f"\n{mode}: {len(ms)} not in top {a.k}")
        for r in ms[:6]:
            print(f"  {r['source'][-24:]}  {r['question']}")


if __name__ == "__main__":
    main()
