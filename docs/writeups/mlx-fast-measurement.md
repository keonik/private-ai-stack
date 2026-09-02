# Why you can't tune a batch-8 M5 benchmark from a single-stream M4

*Notes from a day on the Yukon "MLX Fast" Gemma 4 26B A4B track, 2026-09-01.
The submission itself was one line; the useful output was the measurement
finding. Written for other solvers and for anyone doing inference-performance
work on Apple Silicon.*

## Setup

Target: `mlx-community/gemma-4-26B-A4B-it-qat-4bit`, a sparse MoE (30 layers,
128 routed experts, 8 active per token, tied embeddings, five full-attention
layers on a six-layer repeat). The engine is Swift + vendored MLX + Metal
kernels. Score is a serial-anchored composite over an 8-stream cohort on an M5:

```
composite = prefill_gain^0.25 · decode_gain^0.75
gain      = baseline_aggregate / candidate_aggregate
```

Local machine: M4 Max, 128 GB. Local harness: `./benchmark.sh --local-iterate`
against a public 1024-token golden, 128 decode steps, GPU cool-gated.

## The frontier at clone time

The promoted tip carried ~18.5k lines of prior optimization: custom MMA
quantized GEMV, a two-pass ragged decode attention, a dense-MLP QMV, a rewritten
MoE switch layer, prefill glue, tied-LM-head QMV, plus ~4.4k lines in the
vendored Metal `quantized.h` / `quantized_nax.h` / `sdpa_vector.h` and steel GEMM
twins. Speculative decoding was switched **off** on the frontier
(`speculationEnabled = false`): under the track's sealed serial-target
verification a depth-k round costs 1+k full target forwards, so the adaptive
controller converges to depth 0 and the maintainers pinned it there. Every
committed token came from an ordinary target decode step. The entire 2.26×
came from kernel and engine work.

Frontier score at the time: **2.2569**. Six days in, daily frontier movement had
decayed from +0.27 to +0.05, and the last several promotions were +0.0002 to
+0.015.

## Baseline on this machine

Two runs of the unmodified frontier:

| run | prefill s/tok | decode s/tok | local "est score" |
|---|---|---|---|
| 1 | 0.000847 | 0.017124 | 0.618 |
| 2 | 0.000806 | 0.017142 | 0.625 |

Two things that look like failures and aren't:

1. **est score 0.62 for code that ranks 2.26.** The local harness computes
   speedups against hard-coded *M5 official-runner* constants
   (decode 0.012375 s/tok, prefill 0.000328 s/tok). On an M4 Max that anchor
   makes the leaderboard-leading code look like a 40% regression. Read the
   absolute `*_seconds_per_token` deltas; ignore the local score.
2. **Correctness fails at decode step 3** (expected token 6445, got 236820),
   identically across both runs, on code I hadn't touched. The public goldens
   are M5-generated greedy continuations; a near-tie argmax diverges on another
   silicon generation. Documented caveat, not a regression. Iterate with
   `MLXFAST_LOCAL_ALLOW_GOLDEN_DRIFT=1`.

Variance: decode ±0.1%, prefill ±5%. So the rig *can* resolve a sub-1% decode
change — when the change is reachable at width 1.

## The measurement problem

Three independent gaps stack up between the local rig and the scored run:

**Width.** Local modes are single-stream. There is no local cohort mode; the
CLI exposes no batch-8 entry point. The ranked run is a B=8 cohort, and the
forward pass takes structurally different kernel paths at cohort width (the
MoE expert-sort path engages at 8, not at 1).

**The scored code is width-gated.** The decode async-eval submission ladder —
the thing I ended up changing — is guarded by `batchSize == 8, inputLength == 1`.
At B=1 the function returns `false` before reading the layer set. A local A/B
of that change yields an *identically zero* result, not a small one.

**Kernel variant and generation.** The `_nax` kernels are the M5-generation
variants and the ranked runner selects them. On non-M5 silicon the local run
exercises the plain twin. Even width-agnostic kernel work is measured against
the wrong code.

Net: on this track, from this machine, **no local measurement predicts the
ranked score**, and for the highest-leverage paths no local measurement is
even non-zero.

## What I changed, and why it was a bet

The decode ladder submits the command buffer after selected decoder layers so
the GPU starts while the host is still building later layers. The in-repo
comment documents an M1 Ultra sweep concluding "only the early pair {0,1}
pays; middle boundaries fragment the command buffer." But the shipped switch
was `{0,1,2,3}` — not a row in that table. `git log -L` showed `{0,1}` →
`{0,1,2,3}` landed in an *accepted* submission; the comment was never updated.
So the ranked box had already contradicted the table once: widening the early
window helped.

Change: `{0,1,2,3}` → `{0,1,2,3,4,5}`. One hunk. Token-neutral by construction
(only *when* an already-built graph is submitted changes). Not locally
validatable, and I said so in the public note rather than burning a
cool-gated run on a guaranteed null.

## Lessons

- **Before spending a measured run, check that the change is reachable on the
  local path.** Grep for the gate. If it's `batchSize == N` and you can't run
  N, the run is theater.
- **Don't trust in-repo sweep tables without checking the blame.** They rot the
  moment someone lands a contradicting result.
- **Report the anchor.** A "speedup" against constants from another machine is
  a ratio with a hidden denominator. Print both.
- **Variance first.** Two baseline runs told me decode was tight and prefill
  was noisy; that alone decides which axis is worth measuring.
- **If the frontier is at +0.0002 margins and you can't measure, you are
  buying lottery tickets.** Say so, price it, and move on.
