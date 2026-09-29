#!/usr/bin/env python3
"""Fit one temperature per (question type, option count) on your own labelled rows.

    python3 calibrate.py rows.jsonl                 # fit, report before/after, write calibration.json
    python3 calibrate.py rows.jsonl --dry-run       # report only

Each row: {"state": "...", "question": {...}, "answer": "the correct option name"}

Why this exists: laya's loader warns on every start that the shipped checkpoint "ships temperatures outside
[0.5, 5] ... treat confidence from the affected buckets as uncalibrated", and measured on ag_news it is —
94.5% right with an expected calibration error of 0.195, so a "90%" answer is not 90%. Ranking is already
good (AUC ~0.85 in the CAPTCHA experiment next door); a single scalar per bucket is the whole fix. Fit on
held-out rows of YOUR data: a temperature fitted on someone else's distribution is a guess.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def ece(pairs: list[tuple[float, int]], bins: int = 15) -> float:
    """Expected calibration error: how far confidence sits from accuracy, averaged over confidence bins."""
    if not pairs:
        return 0.0
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        chunk = [(c, ok) for c, ok in pairs if (lo < c <= hi if b else c <= hi)]
        if chunk:
            acc = sum(ok for _, ok in chunk) / len(chunk)
            conf = sum(c for c, _ in chunk) / len(chunk)
            total += len(chunk) / len(pairs) * abs(acc - conf)
    return total


def scaled(probs: dict[str, float], t: float) -> dict[str, float]:
    logits = {k: math.log(max(p, 1e-9)) / t for k, p in probs.items()}
    top = max(logits.values())
    exp = {k: math.exp(v - top) for k, v in logits.items()}
    s = sum(exp.values())
    return {k: v / s for k, v in exp.items()}


def fit(rows: list[tuple[dict, str]]) -> tuple[float, float, float]:
    """Search temperature for the lowest ECE. Coarse then fine: the curve is smooth and one scalar wide."""
    def score(t: float) -> float:
        pairs = []
        for probs, gold in rows:
            p = scaled(probs, t)
            choice = max(p, key=p.get)
            pairs.append((p[choice], int(choice == gold)))
        return ece(pairs)

    best, best_e = 1.0, score(1.0)
    for t in [x / 20 for x in range(10, 101)]:            # 0.5 .. 5.0
        if (e := score(t)) < best_e:
            best, best_e = t, e
    for t in [best + d / 200 for d in range(-10, 11)]:
        if 0.4 < t < 6 and (e := score(t)) < best_e:
            best, best_e = t, e
    return best, score(1.0), best_e


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    dry = "--dry-run" in sys.argv
    sys.path.insert(0, str(Path(__file__).parent))
    from app import bucket  # the service and the fitter must bucket identically

    import laya_mlx

    agent = laya_mlx.Agent(__import__("os").environ.get("LAYA_MODEL", "convaiinnovations/laya"))
    buckets: dict[str, list] = defaultdict(list)
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        q = row["question"]
        answered = agent.predict(row["state"], {"q": q})["answers"]["q"]
        probs = {k: float(v) for k, v in (answered.get("probabilities") or {}).items()}
        if probs:
            buckets[bucket(q)].append((probs, row["answer"]))

    out, report = {}, []
    for name, rows in sorted(buckets.items()):
        t, before, after = fit(rows)
        out[name] = round(t, 3)
        report.append((name, len(rows), before, after, t))

    print(f"{'bucket':16} {'n':>6} {'ECE before':>11} {'ECE after':>10} {'temperature':>12}")
    for name, n, before, after, t in report:
        print(f"{name:16} {n:>6} {before:>11.3f} {after:>10.3f} {t:>12.2f}")
    if dry:
        return 0
    path = Path(__file__).parent / "calibration.json"
    path.write_text(json.dumps({"model": __import__("os").environ.get("LAYA_MODEL", "convaiinnovations/laya"),
                                "fitted_on": src.name, "rows": sum(n for _, n, *_ in report),
                                "temperatures": out}, indent=1) + "\n")
    print(f"\nwrote {path} — restart the service to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
