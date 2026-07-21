# Phase 44 — Headless pipeline runner & metrics

## Title & ID

- **ID:** `phase-44`
- **Title:** Headless pipeline runner & metrics
- **Epic:** Epic 10 — Evaluation Harness
- **Milestone:** M5

## Goal

Run the full pipeline on eval specs **without the UI** and collect the D13 metrics: first-pass vs post-repair pass rate, repair-iteration counts, tokens/₹ cost, and screenshot→URL wall-clock.

## Context

The measurement engine that produces the project's headline evidence (D13). It drives the same handlers the UI drives (design→build→test→repair→deploy→validate), so the numbers reflect the real system, not a mock.

## Depends on

- **Phase IDs:** `phase-23`, `phase-28`, `phase-31`, `phase-37`, `phase-43`
- **External:** Anthropic key; sandbox; (optionally) deploy creds for the deploy/validate legs.

### Prerequisite check

- Core stages exist: codegen (`phase-23`), tests (`phase-28`), repair loop (`phase-31`), deploy (`phase-37`); specs (`phase-43`).

## Detailed tasks

1. **Runner (`app/eval/runner.py`).** For each `EvalSpec`: create a headless project → run stages programmatically via the conductor/handlers (design optional/fake, requirements from the spec, build, test, **repair loop**, deploy, validate). Config toggles which legs run (e.g., skip live deploy in CI, run it in a full pass).
2. **Metrics (`app/eval/metrics.py`).** Capture per spec: `first_pass_pass_rate` (before repair), `post_repair_pass_rate`, `repair_iterations`, `regressions`, `tokens`, `inr_cost`, `screenshot_to_url_seconds` (wall clock), `outcome (delivered|escalated|failed)`. Persist as `Run`/eval records.
3. **Isolation + cleanup.** Each spec runs in its own project/sandbox/DB; reap after; deterministic seeds where possible.
4. **CLI.** `python -m app.eval.runner --spec <id|all> [--with-deploy]` → writes a metrics record set.

## Out of scope for this phase

- The dashboard/report (`phase-45`). Spec format (`phase-43`).

## Acceptance criteria

- Produces a **metrics record per spec** with all D13 fields.
- Captures **first-pass vs post-repair** pass rates and **repair-iteration counts** distinctly (the core contribution evidence).
- Runs headless (no UI); each spec isolated + cleaned up; cost recorded.

## Tests to write

- `backend/tests/eval/test_runner_headless.py` — a small spec runs end-to-end (mock model + sandbox where needed) → metrics record with all fields.
- `backend/tests/eval/test_metrics_fields.py` — first-pass vs post-repair captured separately; cost/iterations present.
- `backend/tests/eval/test_runner_isolation.py` — specs don't cross-contaminate; cleanup runs.

## Manual verification steps

1. `python -m app.eval.runner --spec todo` → metrics record printed/persisted.
2. Inspect: first-pass < post-repair on a spec with a seeded first-pass failure; iterations recorded.
3. `--spec all` (CI-safe legs) → one record per spec.

## Rollback / cleanup notes

- Reap eval projects/sandboxes/DBs; remove eval `Run` records if desired. Live-deploy legs create provider resources — clean them. Reversible.

## Design notes

- Driving the **real handlers** (not reimplementing them) keeps eval honest and cheap to maintain; a config gate keeps costly legs (live deploy) opt-in so CI stays fast/cheap (cost discipline §7).

## Status

✅ done — the measurement engine. A benchmark spec runs through the **real** pipeline with no UI
attached, and comes back as a record carrying every D13 field.

- `app/eval/runner.py` — `EvalRunner.run_spec(spec, *, legs)`:
  `project → requirements (from the spec) → build → test → repair → [deploy] → [validate]`. It
  drives the **same conductor and the same handlers the UI drives** rather than reimplementing them:
  a harness that reimplements the pipeline measures the harness, and drifts the moment a handler
  changes. Requirements go in through the product's own `RequirementsService`, verbatim.
- **The measurement that matters is taken between build and repair.** Tests run once *before* the
  loop (`first_pass`); the loop's own final run supplies `post_repair`. The contribution is therefore
  a difference the runner *observes*, not a claim it repeats.
- `app/eval/metrics.py` — `PassRate`, `SpecMetrics`, `EvalReport` with first-pass/post-repair rates,
  `repair_iterations`, `regressions`, `tokens`/`inr_cost`, `screenshot_to_url_seconds`, wall clock,
  per-stage timings and `outcome (delivered|escalated|failed)`, plus corpus aggregates.
- **The numbers are built to be falsifiable.** An empty run reports `rate=None`, never a flattering
  0% or 100%; `screenshot_to_url_seconds` is `None` when deploy did not run, never `0` (which would
  read as "instant"); crashed specs are excluded from the means rather than counted as zero, so one
  broken spec cannot silently drag the headline down; and a repair that does not help reports
  `delta 0` with `repaired=False` — the metric can say *"the loop did not work"*, which is what makes
  it evidence.
- **Isolation + cleanup.** Each spec gets its own project, sandbox and application database, torn
  down in a `finally`. A pipeline that blows up is a `failed` *record*, not a crashed harness, so one
  bad spec never derails a corpus run — and cleanup still happens for it, because a leaked sandbox
  per failed spec is exactly how a corpus run becomes unrunnable. A cleanup that itself fails is
  logged, never raised: the measurement is the product.
- **Costly legs opt-in** (§7): deploy and validate create billable provider resources, so `Legs()`
  runs build/test/repair and `--with-deploy` adds the rest.
- CLI: `python -m app.eval.runner --spec <id|all> [--with-deploy] [--out path]`, writing a report to
  `eval/results/` (gitignored — reports are measurements of a particular run, not source).

**Acceptance:** a metrics record per spec with all D13 fields; first-pass vs post-repair pass rates
and repair-iteration counts captured **distinctly**; runs headless, each spec isolated and cleaned
up, cost recorded. Covered by `test_runner_headless.py` (12), `test_metrics_fields.py` (11) and
`test_runner_isolation.py` (10).

**Verification:** ruff/black/mypy clean; full backend suite **751 passed** (+31) with the 8 known
pre-existing env-only failures unchanged. All three manual steps run:

1. `--spec todo-list` against the **real** collaborators produced a record and wrote the report.
   With no Docker present the build leg failed and the runner recorded
   `outcome=failed, error="SystemError: Sandbox runtime unavailable", rate=None` — degrading exactly
   as designed instead of crashing, and refusing to invent a 0%.
2. A seeded first-pass failure gave **first-pass 2/5 (40%) → post-repair 5/5 (100%), delta +60%**
   with 3 iterations and 1 regression recorded.
3. The full 9-spec corpus produced one record each, aggregating to
   **first-pass 60% → post-repair 100%**, 9/9 improved, 18 total iterations.

Live-verified **9/9** against real MongoDB, including that an unhelpful repair reports `delta +0%`
and `escalated` rather than `delivered`, that the corpus ran with **9 distinct projects and 9 distinct
application databases**, that cleanup ran for every spec in order, and that the report JSON
round-trips with distinct `first_pass`/`post_repair` blocks — the handoff phase-45 reads.

**Deferred (as scoped):** the dashboard/report is phase-45. Real headline numbers need a full pass
with Docker, an Anthropic key and (for the deploy legs) provider credentials — this phase delivers
the engine that produces them, and its own tests inject the heavy collaborators so the measurement
logic is verified without them.
