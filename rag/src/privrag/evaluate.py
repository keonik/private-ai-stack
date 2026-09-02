"""Eval harness. Input: eval/questions.jsonl with {question, expected_sources:[{source,page}], answer_contains:[...]}.
Reports retrieval recall@k, MRR, citation precision, and (optionally) RAGAS faithfulness/answer relevancy.
Publish these numbers. That's the whole point."""
from __future__ import annotations
import json
from pathlib import Path
from .answer import answer
from .retrieve import Retriever

def run(questions: Path, k: int = 6, rerank: bool = False, with_answers: bool = True) -> dict:
    R = Retriever(); qs = [json.loads(l) for l in questions.read_text().splitlines() if l.strip()]
    recall = mrr = cit_prec = contains = 0.0; n = len(qs); rows = []
    for q in qs:
        hits = R.search(q["question"], k=k, rerank=rerank)
        got = {(h["source"], int(h["page"])) for h in hits}
        want = {(e["source"], int(e["page"])) for e in q.get("expected_sources", [])}
        hit = bool(got & want); recall += hit
        for i, h in enumerate(hits):
            if (h["source"], int(h["page"])) in want: mrr += 1 / (i + 1); break
        cit_prec += (len(got & want) / len(got)) if got else 0
        a = answer(q["question"], hits) if with_answers else ""
        ok = all(s.lower() in a.lower() for s in q.get("answer_contains", [])) if with_answers else None
        contains += bool(ok)
        rows.append({"q": q["question"], "recall_hit": hit, "answer_ok": ok, "answer": a[:300], "top": [(h["source"], int(h["page"])) for h in hits], "rerank_scores": [h.get("rerank") for h in hits] if rerank else None})
    out = {"n": n, "k": k, "rerank": rerank, "recall@k": recall / n, "mrr": mrr / n, "citation_precision": cit_prec / n}
    if with_answers: out["answer_contains_rate"] = contains / n
    out["rows"] = rows
    return out
