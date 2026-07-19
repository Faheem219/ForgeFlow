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

✅ done — the crown-jewel contribution (D1/D7) is now complete end-to-end (29 → 30 → 31).

### Completion notes

- **Controller** [`app/orchestrator/stages/repair.py`](../backend/app/orchestrator/stages/repair.py) —
  `RepairLoopController.run(project, test_run)` iterates *assemble context (29) → patch (30) →
  judge*, stopping on the **first** of: green, regression guard, stall, cap, or a
  budget/cancel/nothing-safe halt.
- **Termination guards.** Cap = `repair_max_iterations`; stall = the failing set failed to *shrink*
  for `repair_stall_threshold` consecutive iterations (an oscillating same-size set counts as no
  progress, and a genuine shrink resets the counter); regression guard trips on the **second**
  attempt that breaks previously-passing tests. Both thresholds are config-resolved
  (`admin > env > default`) so phase-51/52 can tune them and phase-44 can sweep them.
- **Guard precedence (decision):** the regression guard is evaluated before the stall guard — when
  both trip on the same iteration, "my patches keep breaking working code" is the more precise and
  more actionable diagnosis than "no progress".
- **Per-attempt verdict:** `RepairAttempt.outcome ∈ {fixed|no_progress|regressed}`, with regression
  dominating (an attempt that fixes *and* breaks is still the dangerous kind).
- **Escalation is a first-class terminal state, not an error.** Every non-green ending sets the test
  stage `awaiting_user` and emits `repair.escalation` with `{reason, "here's where I'm stuck"
  summary, failing_tests, diffs_tried, metrics, resume}`. `resume` names the contract explicitly —
  Build/`refine` — so guidance re-enters phase-24 and repair can run again. Budget halts and the
  agent's "nothing safe to patch" guard (phase-30's read-only oracle) both land here rather than
  crashing.
- **Audit trail / eval unit:** every iteration is a saved `RepairAttempt` (outcome + diff_ref +
  resulting run); the whole loop is one costed `Run` carrying a new additive `Run.outcome`
  (`fixed|escalated`); the full report is persisted as a versioned `repair_attempt` artifact
  (`kind=repair_report`) for the phase-32 UI and the phase-44 harness. Per-iteration
  `repair.iteration {i, failing_count, regressions, tokens}` events stream throughout.
- **Convergence metrics:** `initial/final_failing`, `failing_by_iteration`, `regressions_introduced`,
  `iterations`, `tokens_spent`, `cost_inr`, `wall_clock_s`.
- **Safety:** cancellable via an `asyncio.Event` checked each iteration; a green run in is a no-op
  out; the controller never mutates outside the sandbox (all writes go through the phase-30 agent).
- **Verification:** ruff/black/mypy clean; a **47-check harness drove the real controller** with
  duck-typed stubs (no Mongo/Docker), covering convergence, stall-at-threshold-not-cap, stall reset,
  oscillation, the two-regression guard, single-regression tolerance, exact iteration counts at
  caps 1/3/4, budget halt, blocked, cancellation, and metric/cost roll-up. The five DB-backed suites
  (`test_repair_{converges,stalls_escalates,regression_guard,bound,audit_trail}.py`) run on Linux/CI.
- **Note on stage transitions:** only *escalation* touches stage state (→ `awaiting_user`, as
  specified). A green loop deliberately leaves the stage to its caller, so it doesn't pre-empt the
  test-stage handler/UI in phase-32.
