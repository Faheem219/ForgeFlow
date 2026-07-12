# Phase 31 — Loop controller (bounded + convergent)

## Title & ID

- **ID:** `phase-31`
- **Title:** Loop controller (bounded + convergent)
- **Epic:** Epic 6 — Self-Healing Repair Loop (core)
- **Milestone:** M3

## Goal

The bounded, no-progress-aware, escalating controller around the repair agent: max iterations, stall detection, regression guard, convergence metrics, human escalation, and a full audit trail (§9 step 4–5).

## Context

The defensible contribution (D1/D7). The invariants are absolute: **never loop unbounded; always leave an auditable trail; always prefer escalation over silent failure.** This is exactly what the eval harness (D13, `phase-44`) measures.

## Depends on

- **Phase IDs:** `phase-30`
- **External:** `REPAIR_MAX_ITERATIONS`, `REPAIR_STALL_THRESHOLD` (config).

### Prerequisite check

- `phase-30` ✅ (repair agent returns results + regressions); config caps resolved (`admin > env > default`).

## Detailed tasks

1. **Controller (`app/orchestrator/stages/repair.py`).** Loop:
   - Start from a failing `TestRun`; each iteration: assemble context (`phase-29`) → repair (`phase-30`) → evaluate.
   - **Bound:** stop at `max_iterations` (default 5).
   - **No-progress detection:** stop if the **failing-test set does not shrink** for `stall_threshold` (default 2) consecutive iterations, **or** if patches introduce regressions **twice**.
   - **Convergence metrics:** track `failing_count` per iteration, `regressions_introduced`, `tokens_spent`, `wall_clock`.
   - **Outcome per attempt:** set `RepairAttempt.outcome ∈ {fixed|no_progress|regressed}`.
2. **Human escalation.** On cap/stall: build an escalation payload `{failing_tests, diffs_tried[], convergence_metrics, "here's where I'm stuck" summary}`; set stage `awaiting_user`; emit `repair.escalation`. **Resume-with-guidance** re-enters Build (`phase-24`) with the user's input, then may re-run repair.
3. **Run record.** The whole loop is one `Run` with cost + final outcome (`fixed|escalated`) — the eval unit (D13).
4. **Events.** Per iteration emit `repair.iteration {i, failing_count, regressions, tokens}`; terminal `repair.done|repair.escalation`.
5. **Idempotency/safety.** Loop is cancellable; respects budget caps (halts → escalates with reason); never mutates outside the sandbox.

## Out of scope for this phase

- The UI (`phase-32`); context/agent internals (`phase-29/30`); live-URL repair reuse (`phase-40`).

## Acceptance criteria

- **Converges** on fixable bugs within the cap (all green) and records `outcome=fixed`.
- **Stalls → escalates:** on a no-progress/regression case, stops at the threshold (not the max) with a clear "stuck" summary and `awaiting_user`.
- **Respects the cap:** never exceeds `max_iterations`; never loops unbounded.
- Full **audit trail**: every iteration is a `RepairAttempt`; the loop is a costed `Run`; metrics present.
- Resume-with-guidance re-enters Build and can resume repair.

## Tests to write

- `backend/tests/orchestrator/stages/test_repair_converges.py` — fixable bug → green within cap; `outcome=fixed`.
- `backend/tests/orchestrator/stages/test_repair_stalls_escalates.py` — non-shrinking failing set → escalation at `stall_threshold`; payload correct.
- `backend/tests/orchestrator/stages/test_repair_regression_guard.py` — two regressions → stop.
- `backend/tests/orchestrator/stages/test_repair_bound.py` — never exceeds `max_iterations`.
- `backend/tests/orchestrator/stages/test_repair_audit_trail.py` — attempts + Run + metrics recorded.

## Manual verification steps

1. Seed a fixable bug → run loop → converges green within N iterations; inspect `RepairAttempt`s + Run cost.
2. Seed an unfixable/oscillating bug → loop stalls → escalation payload surfaces; stage `awaiting_user`.
3. Provide guidance → re-enters Build → optionally resumes repair.

## Rollback / cleanup notes

- Revert repair commits; remove `RepairAttempt`/`Run` records for test projects. Reversible.

## Design notes

- Stall/regression thresholds are **config-resolved** (`admin > env > default`) so they're tunable from the admin dashboard (`phase-51/52`) and swept by the eval harness.
- Escalation is a first-class terminal state (not an error) — "prefer escalation over silent failure" is encoded as a normal outcome.

## Status

⬜ todo
