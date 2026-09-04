"""Before/after eval on data/test.jsonl: exact match of assistant output, JSON validity, and per-field match if JSON."""
from __future__ import annotations
import argparse, json
from mlx_lm import load, generate
ap = argparse.ArgumentParser(); ap.add_argument("--model", required=True); ap.add_argument("--adapter"); ap.add_argument("--split", default="data/test.jsonl"); ap.add_argument("--out", default="eval/results.json"); ap.add_argument("--max-tokens", type=int, default=512)
a = ap.parse_args()
model, tok = load(a.model, adapter_path=a.adapter)
rows = [json.loads(l) for l in open(a.split) if l.strip()]
em = jv = fm = 0; fields = 0; outs = []; per: dict[str, list[int]] = {}
for r in rows:
    msgs = r["messages"][:-1]; want = r["messages"][-1]["content"]
    prompt = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False)
    got = generate(model, tok, prompt=prompt, max_tokens=a.max_tokens, verbose=False).strip()
    em += got == want.strip()
    try:
        g, w = json.loads(got), json.loads(want); jv += 1
        for k, v in w.items():
            fields += 1; hit = g.get(k) == v; fm += hit
            per.setdefault(k, [0, 0]); per[k][0] += hit; per[k][1] += 1
    except Exception: pass
    outs.append({"want": want, "got": got})
n = len(rows); res = {"model": a.model, "adapter": a.adapter, "n": n, "exact_match": em / n, "json_valid": jv / n, "field_match": (fm / fields) if fields else None,
       "per_field": {k: round(v[0] / v[1], 3) for k, v in per.items()}}
json.dump({**res, "samples": outs}, open(a.out, "w"), indent=2); print(res)
