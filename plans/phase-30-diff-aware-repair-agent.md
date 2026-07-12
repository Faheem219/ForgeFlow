# Phase 30 — Diff-aware repair agent

## Title & ID

- **ID:** `phase-30`
- **Title:** Diff-aware repair agent
- **Epic:** Epic 6 — Self-Healing Repair Loop (core)
- **Milestone:** M3

## Goal

A repair agent that patches **only** the failing behavior from the minimal context — without breaking passing tests — applies + git-commits the patch, and re-runs the affected suite then the full suite (§9 steps 2–3).

## Context

Step 2–3 of the crown jewel (D7, §9). The agent sees only the minimal `RepairContext` (`phase-29`), never the whole repo — the discipline that prevents credit-burn bug-loops (D1/D7).

## Depends on

- **Phase IDs:** `phase-21`, `phase-29`
- **External:** `ANTHROPIC_API_KEY`.

### Prerequisite check

- `phase-29` ✅ (context), `phase-21` ✅ (tools), `phase-28` ✅ (runner to re-run).

## Detailed tasks

1. **Repair agent (`app/agents/repair.py`).** Sonnet tool-use loop constrained to the minimal context:
   - Strict system prompt: fix the failing behavior; **do not break passing tests**; touch as few files as possible; explain the patch briefly.
   - Produce edits via `write_file` on `target_files`; **no** access to unrelated files (enforced by only supplying the context).
   - `git_commit("repair: <iteration> <summary>")`.
   - Re-run **affected** suite (fast path), then the **full** suite (`phase-28`); return the new `TestRun` + a patch summary + the diff applied.
2. **Regression awareness.** Compare new results vs prior: compute `newly_failing` (regressions) and `newly_passing`; return both (consumed by the controller's guards, `phase-31`).
3. **Artifacts.** Each attempt → a `RepairAttempt` `{iteration, target_files, diff_ref, resulting_run_id, outcome}` (outcome set by the controller).
4. **Routing/cost.** Sonnet; token/₹ on the `Run`; budget caps respected.

## Out of scope for this phase

- The **loop** (iteration/stall/escalation) — that's `phase-31`. The UI (`phase-32`). Context assembly (`phase-29`).

## Acceptance criteria

- For **seeded bugs**, the agent fixes the failing tests **without introducing regressions**.
- Patches are minimal (few files), committed, and re-run automatically (affected then full).
- Regressions (if any) are detected and reported (`newly_failing`).
- A `RepairAttempt` is recorded with the diff + resulting run.

## Tests to write

- `backend/tests/agents/test_repair_fixes_seeded_bug.py` — seeded failing test → agent patch → green, no regressions (mock model producing a known-good patch; + one real-model integration behind a marker).
- `backend/tests/agents/test_repair_regression_report.py` — a patch that breaks another test → `newly_failing` reported.
- `backend/tests/agents/test_repair_minimality.py` — agent only writes within `target_files`.

## Manual verification steps

1. Seed a bug → run repair once → failing test goes green; full suite re-run; `RepairAttempt` recorded with a diff.
2. Inspect the commit → small, targeted patch.

## Rollback / cleanup notes

- Revert the repair commit(s) to undo patches; `RepairAttempt` artifacts removable. Reversible.

## Design notes

- Regression detection lives in the agent's result (not just the controller) so a single attempt is independently meaningful and testable.

## Status

⬜ todo
