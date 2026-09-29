# laya — typed decisions, 30 ms, on this machine

A decision with a known answer set does not need a language model writing sentences about it. *Which queue
does this ticket go to. Is this lead worth calling. Which of six intents is this. Is this message angry.*
[Laya](https://github.com/NandhaKishorM/laya) (Apache-2.0) is an encoder that scores every allowed answer in
a single forward pass and returns a probability for each — no prompt to maintain, no JSON to coax out of a
chat model, no output parser to break.

```
POST /decide  {"state": "...", "questions": {...}}   →  a choice and a probability per question
GET  /health                                         →  what is loaded, and whether calibration was fitted
GET  /metrics                                        →  Prometheus
```

**Measured here, 2026-09-29** (M4 Max, laya-mlx 0.2.0, one item at a time):

| | ag_news (4 options) | trec (6 options) | per decision |
|---|---|---|---|
| accuracy, held-out rows | 91.7% | 84.0% | **~30 ms** |
| ECE after fitting | 0.049 | 0.102 | |

For comparison, the local chat model answers the same kind of question in 0.5-3 s and has to be told how to
format its reply. This is 30 ms and returns a number.

## Using it

```sh
curl -s localhost:8099/decide -H "Authorization: Bearer $(cat ~/.config/laya/api.key)" \
  -H 'content-type: application/json' -d '{
    "state": "My card was charged twice and nobody has replied in three days.",
    "questions": {
      "intent":  {"type": "choice", "instructions": "What does the customer want?",
                  "criteria": {"refund": "money back", "technical": "a bug or outage",
                               "billing": "an invoice question", "other": "nothing else fits"}},
      "urgent":  {"type": "bool",   "instructions": "Does the message communicate time pressure?"},
      "anger":   {"type": "score",  "instructions": "How angry is the writer?",
                  "criteria": ["calm", "annoyed", "angry"]}
    }}'
```

Every question is answered in the same pass, so asking five costs about what asking one costs. Each answer
comes back as `{choice, confidence, probabilities, calibrated}` — and `calibrated: false` is a warning, not
a detail (see below).

**Question types.** `choice` (named options with a line each on what they mean), `score` (ordered options),
and `bool`. Laya itself knows only choice and score; this service accepts `bool` and asks it as a yes/no
choice, so the same request shape works here and against the `typed-decisions` service on :8094.

**What to put in `state`.** Anything textual: an email body, a ticket, a JSON blob, a transcript. It is an
encoder with a 1024-token window, so send the part that carries the decision, not an entire thread.

## Where it earns its place

- **A gate in front of expensive work.** Decide *whether* to call the 35B model, or which of three prompts
  to use, for 30 ms instead of a second.
- **High-volume triage.** Lead qualification, ticket routing, spam and moderation flags, "does this reply
  need a human" — the cases where you would otherwise run a chat model thousands of times a day.
- **Anywhere you need a number you can threshold.** A confidence that is calibrated lets you say "auto-file
  above 0.9, queue the rest", and know roughly what that costs you.

Where it does **not** belong: anything open-ended (summaries, drafting, extraction of unknown fields), and
any decision whose answer set is not known in advance.

## Calibration is not optional

Laya's own loader warns on every start:

> this checkpoint ships temperatures outside [0.5, 5] which would distort confidence; clamping … treat
> confidence from the affected buckets as uncalibrated.

Measured raw on 200 ag_news items: **94.5% accurate, ECE 0.195** — that is, the probabilities are not what
they say they are. The fix is one scalar per *(question type, option count)* bucket fitted on your own
labelled rows:

```sh
# rows.jsonl: {"state": "...", "question": {...}, "answer": "the correct option name"}
~/.local/share/laya/venv/bin/python calibrate.py rows.jsonl      # writes calibration.json
sudo launchctl kickstart -k system/dev.private-ai-stack.laya     # apply it
```

Fitted on 1,200 rows here: ECE 0.057 → 0.024 for four-option questions, 0.125 → 0.037 for six-option ones.
On rows it had never seen, the four-option bucket held up (0.049, against 0.195 raw); the six-option bucket
landed at 0.102, which is within noise of the raw 0.084 measured on a different slice — so the honest claim
is that fitting helps clearly on the first and is unproven on the second. **Fit on your own data**: a
temperature fitted on somebody else's distribution is a guess, and shipped ones are worse than none.

## Known limits, measured

- **It is only as good as its training mix.** On tasks it has seen, 91-94%. On sst2 sentiment, which it has
  not, **57.7%** — barely above chance for a two-way question. Test on your data before trusting it.
- **No vision.** Image decisions (the CAPTCHA case) need a different path — mlx-vlm logprobs.
- **1024-token context.**
- **One at a time.** ~30 ms per call here; the library claims 7-14 ms batched, which this service does not
  yet do.

## Running it

Installed at `~/.local/share/laya/venv`, key in `~/.config/laya/api.key` (mode 600), served on
127.0.0.1:8099 by `dev.private-ai-stack.laya` — a daemon, so it survives a reboot with nobody logged in:

```sh
sudo ../scripts/install-daemons.sh go     # installs every daemon, including this one
tail -f ~/Library/Logs/private-ai-stack.laya.log
```

The other typed-decision service on :8094 (`~/Development/typed-decisions`) is the US-origin rebuild of the
same idea, trained here. Same request shape; better calibrated on the tasks it was trained on, much weaker
off them. Use that one where provenance matters, this one where breadth does.
