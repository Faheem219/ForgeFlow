# Phase 32 — Repair & test UI

## Title & ID

- **ID:** `phase-32`
- **Title:** Repair & test UI
- **Epic:** Epic 6 — Self-Healing Repair Loop (core)
- **Milestone:** M3

## Goal

Make the repair loop legible and steerable: a test-results panel, live repair attempts (iteration count, shrinking failing set, diffs), an escalation prompt for user guidance, and re-run controls.

## Context

The loop is the contribution (D1); the UI is how a human *watches, trusts, and steers* it (§9 escalation, D12 human-in-the-loop). Completes milestone M3.

## Depends on

- **Phase IDs:** `phase-09`, `phase-28`, `phase-31`
- **External:** —

### Prerequisite check

- `phase-31` ✅ (controller + events), `phase-28` ✅ (results), `phase-09` ✅ (workspace).

## Detailed tasks

1. **Testing feature (`src/features/testing/`).** Test-results panel: per-test pass/fail (grouped by feature/criterion), failure detail (message/assertion/stack/files), stdout viewer, and **Run tests** controls (scope: unit/e2e/all/filter).
2. **Repair view.** Live loop visualization from `repair.iteration` events: iteration timeline, **failing-count sparkline shrinking**, per-iteration **diffs** (Monaco diff), regressions flagged, tokens/₹ + wall-clock.
3. **Escalation UX.** On `repair.escalation`, render the "here's where I'm stuck" summary + diffs tried + a **guidance composer**; submitting resumes via Build (`phase-24`) → optionally re-runs repair.
4. **Wiring.** Mount as the Test stage panel; bind `test.result`, `repair.iteration`, `repair.done|escalation` events; re-run/cancel controls.

## Out of scope for this phase

- Loop logic (`phase-31`), live-URL validation UI (`phase-40`), eval dashboard (`phase-45`).

## Acceptance criteria

- User watches the loop live: failing set shrinking, diffs per iteration, cost accruing.
- On escalation, the user reads the summary + diffs and **submits guidance** that resumes the pipeline.
- Re-run and cancel controls work; results are readable and traceable to criteria.

## Tests to write

- `frontend/src/features/testing/ResultsPanel.test.tsx` — renders per-test results + failure detail (mock).
- `frontend/src/features/testing/RepairView.test.tsx` — streams iterations, shows shrinking count + diffs (mock WS).
- `frontend/src/features/testing/Escalation.test.tsx` — escalation payload → guidance submit resumes (mock API).

## Manual verification steps

1. Run tests with a failure → results panel shows it with detail.
2. Trigger repair → watch iterations + diffs + shrinking failing count + cost.
3. Force escalation → read summary → submit guidance → pipeline resumes.

## Rollback / cleanup notes

- UI-only. Reversible.

## Design notes

- The failing-count sparkline is the single most legible "is it converging?" signal — foregrounded intentionally to make the contribution visible in the demo (D1).

## Status

⬜ todo
