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

✅ done — the evidence view. Phase-44's measurements become something a reviewer can read, quote and
export. **Completes Epic 10.**

- `app/eval/report.py` — reads the run reports phase-44 writes, aggregates them (mean **and median**
  for both pass rates, repair delta, iterations, cost, time-to-URL, wall clock; outcome counts;
  improved-by-repair counts; a **by-difficulty** breakdown so the corpus gradient survives into the
  report), and exports CSV + markdown.
- `app/eval/router.py` — `GET /eval/summary`, `/eval/runs`, `/eval/report.csv`, `/eval/report.md`.
  All read-only and authenticated: **running** the harness is deliberately *not* on the API, so an
  HTTP request can never start a billable corpus run.
- `features/eval/` — the dashboard: a hero **repair-delta** tile with supporting stats, a
  **dumbbell** of first-pass → post-repair per spec, an iteration histogram, a tokens-per-spec bar,
  a time-to-URL chart that appears only once deploy legs have run, the per-spec table, and CSV /
  markdown export buttons. Mounted at `/eval` with a nav entry.

**Two commitments make the output trustworthy.** The CSV schema is **frozen** (`CSV_COLUMNS`,
append-only) — an export whose columns move between runs is useless for comparing results a month
apart, so a test asserts the column list literally. And **missing is never zero**: a spec that
produced no measurement exports an empty cell, charts as "not measured", and tables as "—". A
fabricated 0% would understate the system exactly as badly as a fabricated 100% would flatter it,
and means are taken only over specs that actually produced a number.

**Charts follow the dataviz skill, and the palette was computed rather than chosen.**
`validate_palette.js` was run against ForgeFlow's own chart surface (`#0f172a`) in dark mode:
accent `#6366f1` passes the lightness band, chroma floor and 3:1 contrast; `#64748b` is the
de-emphasis gray (in-band, ≥3:1, and *deliberately* below the chroma floor — it is meant to read
gray). Two shades of one hue could not span the ≥15 normal-vision ΔE floor while staying inside the
dark lightness band — that squeeze is structural for a single hue — so the paired chart uses the
skill's **emphasis** form instead, which is also the more honest encoding: the post-repair number
*is* the story and first-pass is its context. Mark specs are followed (≤24px bars with a 4px
rounded data-end square at the baseline, 2px connectors, ≥8px dots with a 2px surface ring, hairline
recessive gridlines), a legend is always present for the two-series chart, status colors are
reserved and always carry their word, and the table below carries every value — tooltips enhance,
they never gate.

**Acceptance:** the dashboard shows aggregate first-pass vs post-repair, iterations, cost and
time-to-URL; the report exports to CSV and markdown with a stable schema; the numbers match the
underlying phase-44 records. Covered by `test_aggregation.py` (17), `test_export.py` (18),
`EvalDashboard.test.tsx` (14) and `charts.test.tsx` (12).

**Verification:** ruff/black/mypy clean; backend **786 passed** (+35) with the 8 known pre-existing
env-only failures unchanged. Frontend lint/typecheck/format clean, **210 passed** (+26), build
succeeds. Because the palette validator checks colour and not layout, `charts.test.tsx` asserts the
**geometry** numerically instead of eyeballing a render: end labels stay inside the viewBox at 100%,
no bar exceeds the plot, bar thickness stays capped, the longest real spec id (`bookmark-manager`)
fits its gutter, and the chart grows with its row count rather than overlapping rows.

Live-verified **9/9** end-to-end against real MongoDB over real HTTP, driving a **genuine phase-44
report** rather than a hand-written fixture: aggregates equal the underlying records exactly, the
by-difficulty breakdown carries all three grades, the CSV serves 20 stable columns × 9 rows, and the
dashboard, the CSV and the markdown all report the same 50% first-pass headline — the property that
makes the evidence quotable. Unauthenticated access is `401`.

**Deferred (as scoped):** the admin-area surfacing of these same endpoints lands with phase-52.
Real headline numbers still require a full pass with Docker, an Anthropic key and provider
credentials; this phase renders whatever the harness measured.
