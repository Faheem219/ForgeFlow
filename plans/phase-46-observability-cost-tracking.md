# Phase 46 — Observability & cost tracking

## Title & ID

- **ID:** `phase-46`
- **Title:** Observability & cost tracking
- **Epic:** Epic 11 — Hardening, PWA polish & Demo
- **Milestone:** M5

## Goal

Traceable runs and spend visibility: structured logging, per-run traces, a per-project cost dashboard, and alerts when budget caps are hit.

## Context

Cost discipline is a locked concern (§7, D4, risk §12). This phase makes every `Run` traceable with cost and surfaces spend so caps are enforced *visibly*, not silently. Builds on the `Run`/cost plumbing from `phase-20`.

## Depends on

- **Phase IDs:** `phase-20`
- **External:** —

### Prerequisite check

- `phase-20` ✅ (Run/token/₹ accounting + budget caps).

## Detailed tasks

1. **Tracing (`app/core/observability.py`).** Ensure every agent action/tool call/stage run is attached to a `Run` with `{kind, cost:{tokens,inr}, started/finished, events_ref}` (§6); correlate events by `project_id`/`run_id`.
2. **Structured logging.** Consistent JSON logs with correlation ids; **redaction** of secrets (ties to `phase-34`); log levels via config.
3. **Cost API + dashboard.** `GET /projects/{id}/cost` (per-project spend, per-run breakdown, budget headroom); a **cost panel** in the workspace + an overview. Global spend for admins (`phase-52`).
4. **Alerts.** Emit/notify on approaching + hitting `BUDGET_CAP_INR_*` (reuse `budget.halt` from `phase-20`); surface prominently in the UI.
5. **Run explorer.** A simple per-project run/trace list (kind, cost, outcome, duration) linking to details.

## Out of scope for this phase

- The security hardening pass (`phase-47`), resilience (`phase-48`). Metrics *for eval* live in `Epic 10`; this is operational observability.

## Acceptance criteria

- Every run is **traceable** with cost (tokens + ₹) and outcome.
- The cost dashboard shows per-project spend + budget headroom; admins see global spend.
- Budget caps are enforced **visibly** (alert + halt with a clear message); logs never contain secrets.

## Tests to write

- `backend/tests/core/test_run_tracing.py` — actions attach to a Run with cost + correlation.
- `backend/tests/core/test_log_redaction.py` — secrets never appear in logs.
- `backend/tests/core/test_cost_api.py` — per-project + global spend correct.
- `frontend/src/features/.../CostPanel.test.tsx` — renders spend + headroom (mock).

## Manual verification steps

1. Run a few agent actions → cost panel shows spend + per-run breakdown.
2. Approach the cap → alert; hit it → halt + clear message.
3. Grep logs → no secrets.

## Rollback / cleanup notes

- Observability is additive; remove Run/trace records if needed. Reversible.

## Design notes

- Reuses the `Run`/cost model already introduced in `phase-20` — this phase adds *surfaces + alerts + redaction*, not a new accounting system, keeping one source of truth for spend.

## Status

⬜ todo
