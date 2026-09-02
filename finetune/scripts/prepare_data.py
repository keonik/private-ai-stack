"""raw/*.jsonl with {input, output} → data/{train,valid,test}.jsonl in MLX-LM chat format."""
from __future__ import annotations
import json, random, sys
from pathlib import Path
raw, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
SYSTEM = "You are a precise extraction assistant. Output only JSON."
rows = [json.loads(l) for p in raw.glob("*.jsonl") for l in p.read_text().splitlines() if l.strip()]
random.Random(0).shuffle(rows)
n = len(rows); splits = {"train": rows[: int(.8*n)], "valid": rows[int(.8*n): int(.9*n)], "test": rows[int(.9*n):]}
for k, rs in splits.items():
    with (out / f"{k}.jsonl").open("w") as f:
        for r in rs:
            f.write(json.dumps({"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": r["input"]}, {"role": "assistant", "content": r["output"]}]}) + "\n")
    print(k, len(rs))
