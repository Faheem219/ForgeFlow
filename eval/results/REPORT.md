# ForgeFlow — benchmark report

Reproduce with `make eval` (see DEVELOPER.md). Code at `e61918d+dirty`.

## Industry benchmarks

Model `z-ai/glm-5.3-flash` through ForgeFlow's own LLM transport (openai · https://openrouter.ai/api/v1 · upstream pinned to z-ai). Run 2026-10-02T10:02 UTC.

### Code generation — pass@1, then with ForgeFlow's bounded repair

| Benchmark | Problems | pass@1 | + bounded repair | Gain | Repaired | Tokens | Cost (₹) |
|---|---|---|---|---|---|---|---|
| HumanEval (Python) | 164 | **97.0%** | 99.4% | +2.4 pp | 4 | 358,075 | 14.74 |
| MBPP sanitized (Python) | 257 | **96.1%** | 98.8% | +2.7 pp | 7 | 470,639 | 19.82 |
| HumanEval-TS (MultiPL-E) | 159 | **97.5%** | 98.7% | +1.3 pp | 2 | 331,789 | 13.59 |

pass@1 = one greedy sample (temperature 0), graded by the benchmark's own tests — the leaderboard-comparable number. *+ bounded repair* feeds the failing test output back for at most 5 attempts, stopping after 2 without progress (ForgeFlow's `REPAIR_MAX_ITERATIONS` / `REPAIR_STALL_THRESHOLD`); it is a self-repair number, not a pass@1. Code runs in a network-less, capability-dropped container.

Grader check (no model involved): 426/426 reference solutions pass (upstream's for HumanEval/MBPP, 5 hand-written TS controls) and 580/580 always-wrong stubs are rejected.

### Tool calling — BFCL v3 (non-live, Python), official AST checker

| Category | Cases | Accuracy |
|---|---|---|
| simple | 400 | 96.2% |
| multiple | 200 | 93.0% |
| parallel | 200 | 91.5% |
| parallel_multiple | 200 | 90.0% |
| **Non-live AST mean** | — | **92.7%** |
| irrelevance (correctly calls nothing) | 240 | 68.8% |

689,982 tokens · ₹16.44 · 0 provider errors. Mean = unweighted mean of the four AST categories (not the leaderboard's *Overall*, which also includes Java/JS, live and multi-turn).

Total: 1,853 model calls · 1,850,485 tokens · ₹64.58 · 110 min.

<sub>Source: `eval/results/bench/bench-1790941973.json`</sub>

> **Partial run: 2 of 10 specs.** Do not read it as a corpus result.

## End-to-end app generation (spec corpus)

**2 specs** · 0 delivered · 2 escalated · 0 failed

### Headline

| Metric | Mean | Median |
|---|---|---|
| Acceptance criteria met, first pass | 75% | 75% |
| Acceptance criteria met, post repair | 75% | 75% |
| First-pass pass rate | 88% | 88% |
| Post-repair pass rate | 88% | 88% |
| Repair delta | +0% | +0% |
| Repair iterations | 0 | 0 |
| Tokens per spec | 1493038 | 1493038 |
| Cost per spec (₹) | 22.89 | 22.89 |
| Screenshot → URL (s) | — | — |

Repair improved **0 of 2** scored specs; 0 were already green first-pass and 0 were green after repair. Generated tests cover 100% of 16 acceptance criteria; 0 specs meet every criterion after repair.

### By difficulty

| Difficulty | Specs | First-pass | Post-repair |
|---|---|---|---|
| moderate | 1 | 89% | 89% |
| simple | 1 | 87% | 87% |

### Per spec

| Spec | Difficulty | Outcome | Criteria first → post | Tests first → post | Iterations | Tokens | ₹ |
|---|---|---|---|---|---|---|---|
| notes-crud | simple | escalated | 71% → 71% | 87% → 87% | 0 | 1376919 | 20.85 |
| bookmark-manager | moderate | escalated | 78% → 78% | 89% → 89% | 0 | 1609157 | 24.93 |

Model `z-ai/glm-5.3-flash` (codegen) / `z-ai/glm-5.3-flash` (routing). *Criteria met* = share of each spec's acceptance criteria verified by a passing test the product's test-gen agent wrote from the spec; untested criteria count as unmet. *Tests* = every test in the workspace, skeleton included.

<sub>Source: `eval/results/eval-1790946244.json` · legs: build, testgen, test, repair</sub>
