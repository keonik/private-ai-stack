"""Score sidecars against the generator's ground truth.

    python scripts/score_synthetic.py out/synthetic-oh1/labels.json out/synthetic-oh1 [more dirs...]

Prints a per-field accuracy table per directory, and for the booleans the false positives and false
negatives separately — an average hides which way a mistake goes, and on these forms the direction is
the whole story.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TEXT = ["report_number", "crash_datetime", "county_code", "locality", "reporting_agency", "officer_name"]
BOOL = ["animal_involved", "pedestrian_or_cyclist_involved", "alcohol_or_drugs_suspected", "injury_mentioned"]


import re


def norm(v):
    return str(v).strip().lower() if v is not None else ""


def loose(v):
    """Case, punctuation and possessive differences are formatting, not extraction errors:
    "Licking County Sheriff's Office" is the same answer as "LICKING COUNTY SHERIFFS OFFICE"."""
    # strip apostrophes first, so "Sheriff's" becomes "Sheriffs" rather than "Sheriff s"
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"['\u2019]", "", norm(v))).strip()


def score(labels: dict, d: Path) -> dict:
    res = {k: [0, 0, 0] for k in TEXT}  # exact, total, loose
    conf = {k: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for k in BOOL}
    misses: list[str] = []
    n = 0
    for name, truth in labels.items():
        side = d / (name + ".meta.json")
        if not side.exists():
            continue
        n += 1
        got = json.loads(side.read_text())["fields"]
        for k in TEXT:
            ok = norm(got.get(k)) == norm(truth[k])
            res[k][0] += ok
            res[k][1] += 1
            res[k][2] += loose(got.get(k)) == loose(truth[k])
            if loose(got.get(k)) != loose(truth[k]) and len(misses) < 8:
                misses.append(f"{name} {k}: got {got.get(k)!r} want {truth[k]!r}")
        for k in BOOL:
            g, t = bool(got.get(k)), bool(truth[k])
            conf[k]["tp" if (g and t) else "fp" if (g and not t) else "fn" if t else "tn"] += 1
    return {"n": n, "text": res, "bool": conf, "misses": misses}


def main() -> None:
    labels = json.loads(Path(sys.argv[1]).read_text())
    dirs = [Path(x) for x in sys.argv[2:]]
    runs = {d.name: score(labels, d) for d in dirs}
    names = list(runs)
    print(f"n = {runs[names[0]]['n']} synthetic reports\n")
    print("| field | " + " | ".join(names) + " |")
    print("|---" * (len(names) + 1) + "|")
    for k in TEXT:
        cells = []
        for m in names:
            e, n_, l = runs[m]["text"][k]
            cells.append(f"{e}/{n_}" if e == l else f"{l}/{n_} ({e} exact)")
        print(f"| {k} | " + " | ".join(cells) + " |")
    for k in BOOL:
        cells = []
        for m in names:
            c = runs[m]["bool"][k]
            wrong = c["fp"] + c["fn"]
            cells.append(f"{c['tp'] + c['tn']}/{sum(c.values())}" + (f" ({c['fp']} FP, {c['fn']} FN)" if wrong else ""))
        print(f"| {k} | " + " | ".join(cells) + " |")
    for m in names:
        r = runs[m]
        tot = sum(v[2] for v in r["text"].values()) + sum(c["tp"] + c["tn"] for c in r["bool"].values())
        den = sum(v[1] for v in r["text"].values()) + sum(sum(c.values()) for c in r["bool"].values())
        print(f"\n{m}: {tot}/{den} field values correct ({tot / den:.3f}), ignoring case and punctuation")
        for s in r["misses"] or ["(no substantive misses)"]:
            print("   ", s)


if __name__ == "__main__":
    main()
