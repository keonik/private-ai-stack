"""Per-document-kind preprocessing: hand the model only the text that carries the answer.

Form PDFs flatten into a stream of code legends ("1 - FATAL", "98 - ANIMAL") interleaved with values. Models
of every size then pick legend text as evidence. For kinds with a known layout, pull the labelled values with
regexes anchored on the printed captions and pass those plus the free-text sections. If the anchors are not
found (a different form generator, a different agency's template) fall back to the full text so nothing is lost.
"""
from __future__ import annotations
import re
from collections.abc import Callable


def _grab(t: str, pat: str) -> str | None:
    m = re.search(pat, t, re.S)
    return m.group(1).strip() if m else None


_OH1_FIELDS = {
    "LOCAL REPORT NUMBER": r"LOCAL REPORT NUMBER \*\s*\n(.+?)\n",
    "REPORTING AGENCY NAME": r"REPORTING AGENCY NAME \*\s*\n(.+?)\n",
    "COUNTY": r"COUNTY\*\s*\n(\d+)",
    "LOCATION (CITY, VILLAGE, TOWNSHIP)": r"LOCATION: \s*\nCITY, VILLAGE, TOWNSHIP\s*\n\*\s*\n(.+?)\n",
    "CRASH DATE / TIME": r"CRASH DATE / TIME\*\s*\n(.+?)\n",
    "OFFICER'S NAME": r"OFFICER'S NAME\*\s*\n(.+?)\n",
}


def prep_oh1(text: str) -> str:
    got = {k: _grab(text, p) for k, p in _OH1_FIELDS.items()}
    narrative = _grab(text, r"NARRATIVE\s*\n(.+?)\nREPORT TAKEN BY")
    if not got["LOCAL REPORT NUMBER"] or narrative is None:
        return text
    head = "\n".join(f"{k}: {v or ''}" for k, v in got.items())
    return f"{head}\n\nNARRATIVE:\n{narrative}\n"


PREP: dict[str, Callable[[str], str]] = {"crash_oh1": prep_oh1}
