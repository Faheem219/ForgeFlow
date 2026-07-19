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

✅ done — **completes M3**: requirements → tests → bounded self-healing repair, end to end.

### Completion notes

- **`src/features/testing/`** — four pieces mounted as the Test stage panel (`StagePanel`'s panel
  registry):
  - [`ResultsPanel`](../frontend/src/features/testing/ResultsPanel.tsx): per-test results grouped by
    source file, each tagged with its **criterion id** (traceability all the way back to phase-25),
    expandable failure detail (message / stack / implicated files), an on-demand reporter-output
    viewer, and Run-tests controls (scope + name filter).
  - [`RepairView`](../frontend/src/features/testing/RepairView.tsx): the **failing-count sparkline**
    foregrounded as the design note asks — it turns green when the trail descends — plus the
    iteration timeline with per-attempt verdicts, on-demand diffs, regressions, tokens/₹ and wall
    clock. While the loop runs, live `repair.iteration` events drive the trail; afterwards the
    persisted report does (so a reload keeps everything).
  - [`Escalation`](../frontend/src/features/testing/Escalation.tsx): the human-in-the-loop moment —
    reason, "here's where I'm stuck" summary, still-failing tests, what was already tried, and a
    guidance composer that submits through the **resume contract the controller itself names**
    (`escalation.resume` → Build/`refine`), then follows the pipeline to Build.
  - [`TestingPanel`](../frontend/src/features/testing/TestingPanel.tsx): composes them + stage
    proceed/skip.
- **Backend wiring (task 4)** — the UI needed a surface that didn't exist yet, so this phase adds
  [`app/testing/repair_router.py`](../backend/app/testing/repair_router.py): `POST /repair/run`,
  `POST /repair/cancel`, `GET /repair/latest`, `GET /repair/attempts/{id}/diff`, plus
  `GET /tests/runs/{id}/stdout` on the testing router. Phase-31's report now carries each attempt's
  `id` so the diff endpoint is addressable.
- **Cancel is real, not cosmetic.** An in-process registry hands the controller an `asyncio.Event`;
  the loop checks it between iterations, so cancelling takes effect at the next iteration boundary
  and never mid-patch (which keeps the workspace consistent). Single-instance, same documented
  caveat as the realtime hub.
- **Diff rendering decision:** the plan suggested a Monaco diff, but phase-30 stores a **unified
  diff** (what the agent actually applied). Rendering that directly — line-tinted +/−/@@ — is
  truer to the artifact and far lighter than reconstructing two documents for a side-by-side editor.
- **Verification:** frontend **111/111 vitest** (17 new across `ResultsPanel`/`RepairView`/
  `Escalation`), ESLint + `tsc` clean; backend ruff/black/mypy clean; a route smoke-test confirmed
  the app boots with all five new endpoints registered and nothing pre-existing clobbered.
