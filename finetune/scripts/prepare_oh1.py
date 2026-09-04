"""Build a fine-tuning set from the OH-1 sidecars: input = the same reduced text the extractor saw
(page 1 → caption-anchored header + narrative, extract/src/privextract/prep.py), output = the verified
field values as compact JSON (no confidence/evidence).

    python scripts/prepare_oh1.py ../stack/data/inbox oh1/raw   # → oh1/raw/oh1.jsonl {input, output}
    python scripts/prepare_data.py oh1/raw oh1/data              # → train/valid/test (80/10/10, seed 0)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extract/src"))
from privextract.prep import prep_oh1  # noqa: E402
from pypdf import PdfReader  # noqa: E402

inbox, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
n = 0
with (out / "oh1.jsonl").open("w") as f:
    for meta in sorted(inbox.glob("*.meta.json")):
        d = json.loads(meta.read_text())
        if d.get("kind") != "crash_oh1":
            continue
        pdf = inbox / d["source"]
        if not pdf.exists():
            continue
        text = PdfReader(str(pdf)).pages[0].extract_text() or ""
        fields = {k: v for k, v in d["fields"].items()}
        f.write(json.dumps({"input": prep_oh1(text), "output": json.dumps(fields, separators=(",", ":"))}) + "\n")
        n += 1
print(f"wrote {n} examples to {out/'oh1.jsonl'}")
