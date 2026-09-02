"""FastAPI: POST /extract/{kind} with a PDF or text → JSON + review flags. GET /review lists queued docs. POST /export → xlsx."""
from __future__ import annotations
import io, json, os, uuid
from pathlib import Path
import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from openpyxl import Workbook
from pypdf import PdfReader
from .extract import extract, needs_review, validate
from .schemas import SCHEMAS

QUEUE = Path(os.environ.get("QUEUE_DIR", "./queue")); QUEUE.mkdir(exist_ok=True)
RECTO_URL = os.environ.get("RECTO_URL", "").strip()
app = FastAPI(title="privextract", version="0.1.0")

def _text(name: str, data: bytes) -> str:
    if name.lower().endswith(".pdf"):
        t = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        if len(t.strip()) < 50 and RECTO_URL:  # scanned → OCR
            r = httpx.post(RECTO_URL, files={"file": (name, data)}, timeout=600); r.raise_for_status()
            t = "\n".join(p.get("text", "") for p in r.json().get("pages", []))
        return t
    return data.decode(errors="ignore")

@app.post("/extract/{kind}")
async def do_extract(kind: str, file: UploadFile = File(...)):
    if kind not in SCHEMAS: raise HTTPException(404, f"unknown kind; choose from {list(SCHEMAS)}")
    obj = extract(_text(file.filename or "doc", await file.read()), kind)
    issues = validate(obj); flags = needs_review(obj, issues)
    rec = {"id": str(uuid.uuid4()), "kind": kind, "file": file.filename, "data": obj.model_dump(mode="json"), "issues": issues, "review": flags, "status": "review" if flags else "ok"}
    (QUEUE / f"{rec['id']}.json").write_text(json.dumps(rec, indent=2))
    return rec

@app.get("/review")
def review_list():
    return [json.loads(p.read_text()) for p in sorted(QUEUE.glob("*.json"))]

@app.post("/review/{doc_id}/approve")
def approve(doc_id: str, data: dict):
    p = QUEUE / f"{doc_id}.json"
    if not p.exists(): raise HTTPException(404)
    rec = json.loads(p.read_text()); rec["data"] = data; rec["status"] = "approved"; rec["review"] = []
    p.write_text(json.dumps(rec, indent=2)); return rec

@app.get("/export")
def export():
    wb = Workbook(); ws = wb.active; ws.title = "extracted"; hdr: list[str] = []
    for p in sorted(QUEUE.glob("*.json")):
        rec = json.loads(p.read_text()); flat = {"file": rec["file"], "kind": rec["kind"], "status": rec["status"]}
        flat.update({k: (v.get("value") if isinstance(v, dict) else v) for k, v in rec["data"].items() if k != "line_items"})
        if not hdr: hdr = list(flat); ws.append(hdr)
        ws.append([flat.get(h) for h in hdr])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=extracted.xlsx"})

@app.get("/", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent.parent.parent / "review" / "index.html").read_text()

def main():
    import uvicorn; uvicorn.run("privextract.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), reload=False)
