#!/usr/bin/env python3
"""What a sound made over the reply is judged to be. `python3 verdict_test.py` — no test framework needed.

These rules are heuristics, not a standard, and this file is where they are pinned down: the reply's own
words coming back through the speakers must never read as an interruption, "mm-hmm" must not stop the Mac,
and anything that is actually a new request must.
"""
import os
import sys

os.environ.setdefault("INFER_BASE_URL", "unused")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import backchannel_verdict  # noqa: E402

SAID = ("Once upon a time a lonely lighthouse stood on a rocky cliff, its beam cutting through the dark "
        "storm to guide lost ships safely home.")
CASES = [
    # (what the microphone heard, what the Mac was saying, expected)
    ("its beam cutting through the dark", SAID, "echo"),
    ("stood on a rocky cliff", SAID, "echo"),
    ("guide lost ships safely home", SAID, "echo"),
    ("Yeah.", SAID, "backchannel"),
    ("Mm-hmm.", SAID, "backchannel"),
    ("okay okay", SAID, "backchannel"),
    ("", SAID, "ambient"),
    ("What about the moon?", SAID, "interrupt"),
    ("Actually stop. What is the capital of France?", SAID, "interrupt"),
    ("no that is wrong", SAID, "interrupt"),
    ("tell me about the storm", SAID, "interrupt"),   # shares words, but is a request: overlap is below 0.6
    ("Yeah.", "", "backchannel"),                      # nothing spoken yet: still not an interruption
    ("What about the moon?", "", "interrupt"),
]

fails = 0
for heard, said, want in CASES:
    got = backchannel_verdict(heard, said)
    flag = "ok " if got == want else "FAIL"
    if got != want:
        fails += 1
    print(f"{flag} {heard[:44]!r:48} -> {got:12} (expected {want})")
print(f"\n{len(CASES) - fails}/{len(CASES)} as expected")
sys.exit(1 if fails else 0)
