# Phase 45 — Results dashboard/report

## Title & ID

- **ID:** `phase-45`
- **Title:** Results dashboard/report
- **Epic:** Epic 10 — Evaluation Harness
- **Milestone:** M5

## Goal

Present the evaluation evidence: aggregate metrics with charts and an exportable report (CSV/markdown) suitable for a paper/pitch.

## Context

Turns `phase-44` metrics into the communicable artifact D13 promises. This is the "evidence view" that makes the repair-loop contribution (D1) legible to reviewers.

## Depends on

- **Phase IDs:** `phase-44`
- **External:** —

### Prerequisite check

- `phase-44` ✅ (metrics records exist).

## Detailed tasks

1. **Aggregation (`app/eval/report.py`).** Aggregate across specs/runs: mean/median first-pass vs post-repair pass rate, repair-iteration distribution, cost per spec, screenshot→URL time distribution, delivered vs escalated counts. `GET /eval/summary`, `GET /eval/runs`.
2. **Export.** CSV + markdown report generation (`GET /eval/report.(csv|md)`); stable schema for external analysis.
3. **Dashboard (`src/features/eval/`).** Charts (first-pass vs post-repair bar, iteration histogram, cost, time-to-URL), a per-spec table, and an **Export** button. (Follow the dataviz guidance when building charts.)
4. **Access.** Available to the user; admin-visible in the admin area (`phase-52`) too.

## Out of scope for this phase

- Running the pipeline (`phase-44`), spec authoring (`phase-43`).

## Acceptance criteria

- Dashboard shows **aggregate** metrics (first-pass vs post-repair, iterations, cost, time-to-URL).
- Report **exports** to CSV and markdown with a stable schema.
- Numbers match the underlying `phase-44` records.

## Tests to write

- `backend/tests/eval/test_aggregation.py` — aggregates computed correctly from fixture runs.
- `backend/tests/eval/test_export.py` — CSV/markdown export shape + values.
- `frontend/src/features/eval/EvalDashboard.test.tsx` — renders charts + table from mock summary.

## Manual verification steps

1. Run `phase-44` on several specs → open the eval dashboard → charts populated.
2. Export CSV + markdown → values match the dashboard.

## Rollback / cleanup notes

- Read-only over eval records; no destructive state. Reversible.

## Design notes

- Charts follow the **dataviz** skill (consistent, accessible, theme-aware) so the evidence looks credible in a paper/demo; export keeps a stable schema so results survive UI changes.

## Status

⬜ todo
