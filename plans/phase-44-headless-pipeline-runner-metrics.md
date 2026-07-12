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

⬜ todo
