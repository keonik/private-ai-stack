"""Batch extraction to metadata sidecars.

    python scripts/batch.py --kind crash_oh1 --pages 1 --workers 3 /path/to/pdfs

For every PDF writes `<name>.meta.json` next to it: flattened field values, per-field confidence, and the
review flags from `needs_review`. Existing sidecars are skipped, so re-runs only do new files. The sidecar
is what `stack/rag-ingest` reads to make the fields filterable.
"""
from __future__ import annotations
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from privextract.extract import extract, validate, needs_review, MODEL  # noqa: E402
from privextract.prep import PREP  # noqa: E402


def text_of(path: Path, pages: int | None) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text(errors="ignore")
    r = PdfReader(str(path))
    ps = r.pages[:pages] if pages else r.pages
    return "\n".join((p.extract_text() or "") for p in ps)


def one(path: Path, kind: str, pages: int | None, no_prep: bool = False) -> tuple[Path, float, list[str]]:
    t0 = time.time()
    raw = text_of(path, pages)
    text = raw if no_prep else PREP.get(kind, lambda x: x)(raw)
    obj = extract(text, kind)
    issues = validate(obj)
    flags = needs_review(obj, issues)
    d = obj.model_dump()
    side = {"kind": kind, "source": path.name, "model": MODEL, "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "fields": {k: v["value"] for k, v in d.items() if isinstance(v, dict) and "value" in v},
            "confidence": {k: v["confidence"] for k, v in d.items() if isinstance(v, dict) and "confidence" in v},
            "evidence": {k: v["evidence"] for k, v in d.items() if isinstance(v, dict) and "evidence" in v},
            "review": flags, "seconds": round(time.time() - t0, 1), "prep": "reduced" if text is not raw else "full", "input_chars": len(text)}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(side, indent=1, default=str))
    return path, side["seconds"], flags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir"); ap.add_argument("--kind", required=True); ap.add_argument("--pages", type=int)
    ap.add_argument("--no-prep", action="store_true", help="skip caption-anchored preprocessing (for measuring what it is worth)")
    ap.add_argument("--workers", type=int, default=2); ap.add_argument("--limit", type=int); ap.add_argument("--glob", default="*.pdf")
    ap.add_argument("--only-flagged", action="store_true", help="re-extract files whose sidecar has review flags (escalation pass with a bigger model)")
    ap.add_argument("--where", help='JSON: re-extract files whose sidecar fields match ANY key, e.g. \'{"alcohol_or_drugs_suspected": true}\'. Use to verify rare positives with a bigger model.')
    a = ap.parse_args()
    where = json.loads(a.where) if a.where else None
    def wanted(p: Path) -> bool:
        side = p.with_name(p.name + ".meta.json")
        if not side.exists():
            return not (a.only_flagged or where)
        if where:
            try:
                d = json.loads(side.read_text())
                if d.get("model") == MODEL:
                    return False  # already done by this model
                return any(d.get("fields", {}).get(k) == v for k, v in where.items())
            except Exception:
                return True
        if a.only_flagged:
            try:
                return bool(json.loads(side.read_text()).get("review"))
            except Exception:
                return True
        return False
    files = sorted(p for p in Path(a.dir).glob(a.glob) if wanted(p))
    if a.limit: files = files[: a.limit]
    print(f"{len(files)} files to extract ({a.kind}, pages={a.pages}, workers={a.workers})", flush=True)
    t0, done, flagged, errors = time.time(), 0, 0, 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(one, p, a.kind, a.pages, a.no_prep): p for p in files}
        for f in as_completed(futs):
            try:
                p, secs, flags = f.result(); done += 1; flagged += bool(flags)
                print(f"[{done}/{len(files)}] {p.name} {secs}s {'REVIEW ' + ','.join(flags) if flags else 'ok'}", flush=True)
            except Exception as e:
                errors += 1; print(f"ERROR {futs[f].name}: {str(e)[:200]}", flush=True)
    el = time.time() - t0
    print(f"done={done} flagged={flagged} errors={errors} wall={el:.0f}s rate={done / el * 60:.1f}/min", flush=True)


if __name__ == "__main__":
    main()
