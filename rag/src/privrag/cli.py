from __future__ import annotations
import json
from pathlib import Path
import typer
from rich import print as rprint
app = typer.Typer(help="privrag: ingest, search, ask, eval")

@app.command()
def ingest(paths: list[Path], acl: str = "*"):
    from .ingest import ingest_paths
    files = [p for x in paths for p in (x.rglob("*") if x.is_dir() else [x]) if p.is_file()]
    rprint(f"indexed {ingest_paths(files, acl)} chunks from {len(files)} files")

@app.command()
def search(q: str, k: int = 6, rerank: bool = False):
    from .retrieve import Retriever
    for h in Retriever().search(q, k, rerank=rerank):
        rprint(f"[bold]{h['source']} p.{h['page']}[/] score={h['score']:.4f}\n  {h['text'][:200]}…")

@app.command()
def ask(q: str, k: int = 6, rerank: bool = False):
    from .retrieve import Retriever; from .answer import answer
    hits = Retriever().search(q, k, rerank=rerank)
    rprint(answer(q, hits)); rprint("\n[dim]sources:[/]", [(h["source"], h["page"]) for h in hits])

@app.command()
def eval(questions: Path = Path("eval/questions.jsonl"), k: int = 6, rerank: bool = False, no_answers: bool = False, out: Path = Path("eval/results.json")):
    from .evaluate import run
    res = run(questions, k, rerank, with_answers=not no_answers)
    out.write_text(json.dumps(res, indent=2))
    rprint({k_: v for k_, v in res.items() if k_ != "rows"}); rprint(f"[dim]wrote {out}[/]")

if __name__ == "__main__":
    app()
