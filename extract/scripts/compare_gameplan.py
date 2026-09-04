"""Score LLM-extracted sidecars against a rule-based parser's output (scripts/gameplan_dump.ts).

    python scripts/compare_gameplan.py gameplan.jsonl ../stack/data/inbox

Fields both sides produce are compared exactly (after normalisation); booleans get a confusion matrix.
The rule-based side reads coded boxes (injury severity per unit, unit-in-error 98 = animal) that the LLM
only sees through the narrative, so those rows measure narrative-vs-form agreement, not LLM error alone.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def norm_name(s: str | None) -> set[str]:
    toks = set(re.findall(r"[a-z]{2,}", (s or "").lower()))
    return toks - {"tpr", "ptl", "ofc", "sgt", "lt", "cpl", "dep", "det", "officer", "trooper", "deputy", "po", "jr", "sr"}


def main(gp_path: str, inbox: str) -> None:
    gp = {r["file"]: r for r in map(json.loads, Path(gp_path).read_text().splitlines()) if r.get("error") is None}
    rows = []
    for p in sorted(Path(inbox).glob("*.meta.json")):
        d = json.loads(p.read_text())
        key = d["source"].split("oh1-2026-08-03-", 1)[-1]
        if key in gp:
            rows.append((d, gp[key]))
    print(f"paired: {len(rows)} sidecars with parser output\n")

    exact: dict[str, Counter] = {k: Counter() for k in ("report_number", "crash_date", "crash_datetime", "county_code", "officer_name")}
    conf: dict[str, Counter] = {"injury (LLM narrative vs coded severity<5)": Counter(), "animal (LLM narrative vs unit-in-error 98)": Counter()}
    misses: dict[str, list] = {k: [] for k in exact}
    for d, g in rows:
        f = d["fields"]
        # report number
        a, b = (f.get("report_number") or "").strip().upper(), (g.get("reportNumber") or "").strip().upper()
        exact["report_number"][a == b] += 1
        if a != b: misses["report_number"].append((d["source"], a, b))
        # date/time: LLM gives local wall clock "MM/DD/YYYY HH:MM"; parser gives UTC ISO
        try:
            la = datetime.strptime(f.get("crash_datetime") or "", "%m/%d/%Y %H:%M").replace(tzinfo=ET)
            lb = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(ET)
            exact["crash_date"][la.date() == lb.date()] += 1
            ok = la == lb
        except Exception:
            exact["crash_date"][False] += 1; ok = False
        exact["crash_datetime"][ok] += 1
        if not ok: misses["crash_datetime"].append((d["source"], f.get("crash_datetime"), g.get("date")))
        # county code
        ok = f.get("county_code") == g.get("countyCode")
        exact["county_code"][ok] += 1
        if not ok: misses["county_code"].append((d["source"], f.get("county_code"), g.get("countyCode")))
        # officer: token overlap (parser keeps rank/badge, LLM writes "Last, First")
        na, nb = norm_name(f.get("officer_name")), norm_name(g.get("officerName"))
        ok = bool(na and nb) and (na <= nb or nb <= na or len(na & nb) >= 2)
        exact["officer_name"][ok] += 1
        if not ok: misses["officer_name"].append((d["source"], f.get("officer_name"), g.get("officerName")))
        # booleans
        coded_injury = any((u.get("injurySeverity") or 5) < 5 for u in g.get("units", []))
        conf["injury (LLM narrative vs coded severity<5)"][(bool(f.get("injury_mentioned")), coded_injury)] += 1
        conf["animal (LLM narrative vs unit-in-error 98)"][(bool(f.get("animal_involved")), g.get("unitInErrorNumber") == 98)] += 1

    print("| field | agree | n | rate |\n|---|---|---|---|")
    for k, c in exact.items():
        n = sum(c.values()); print(f"| {k} | {c[True]} | {n} | {c[True]/n:.3f} |")
    print()
    for k, c in conf.items():
        tp, fp, fn, tn = c[(True, True)], c[(True, False)], c[(False, True)], c[(False, False)]
        print(f"{k}: LLM=true&coded=true {tp}, LLM=true&coded=false {fp}, LLM=false&coded=true {fn}, both false {tn}")
    print()
    for k, m in misses.items():
        if m:
            print(f"-- {k}: {len(m)} disagreements (first 8)")
            for row in m[:8]: print("  ", row)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
