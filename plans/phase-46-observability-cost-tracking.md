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

✅ done — spend is now **visible** rather than silently enforced, and every unit of work is
traceable. Opens Epic 11. Per the design note this adds *surfaces, alerts and correlation* on top of
phase-20's `Run`/cost model — there is no second accounting system.

- `app/core/observability.py` — `traced_run()` opens (or **adopts**) a `Run`, binds correlation, and
  **always closes it**, including when the work raises: an un-finished `Run` understates spend, looks
  like it is still going, and quietly corrupts the budget guard that sums those records. A
  `CorrelationFilter` stamps `project_id`/`run_id`/`run_kind` onto every log record emitted inside a
  run, so a line written deep in a tool call is traceable without threading ids through every call
  site. Nested runs restore the outer context on exit.
- **Budget warning (new).** Phase-20 halted *at* the cap with no prior signal. `budget_warn_ratio`
  (default 0.8) adds a `budget.warning` event and a `warning` flag on the API, so approaching a cap
  is visible before work stops.
- `app/core/cost_router.py` — `GET /projects/{id}/cost` (spend, **headroom**, per-kind breakdown),
  `GET /projects/{id}/runs` (the run explorer: kind, cost, outcome, duration), and
  `GET /admin/cost` (global spend, top projects — admin only). An **uncapped** budget reports
  `headroom=null`, never `0`, because "unlimited" and "exhausted" must not look alike.
- `features/cost/CostPanel.tsx` — mounted in the workspace beside the stage navigator. Budget state
  is said **in words** as well as colour ("Approaching cap", "Halted — runs are blocked until the cap
  is raised"): a halt is exactly when a user needs to know *why* work stopped, and a red bar does not
  say that. Refreshes on `budget.warning`/`budget.halt` rather than showing a stale number.

**Two real bugs were found and fixed while building this:**

1. **Over-redaction blinded the cost trail.** `SENSITIVE_KEY_RE` matched `token` as a substring, so
   `tokens=1500` — a *count* — was logged as `***redacted***`. That would have gutted the very trail
   this phase exists to provide, while protecting nothing. Fixed with a narrow exemption
   (`SAFE_COUNT_KEY_RE`) that applies **only when the value is numeric**, so `tokens="sk-..."` is
   still redacted. Covered both ways.
2. **The run explorer's order was arbitrary.** `list_for_project` sorted on `started_at` alone, which
   Mongo stores at millisecond precision — two runs started in the same millisecond tied. Caught by
   the *full* suite (it passed in isolation), and fixed with an `_id` tie-break, the same class of
   bug as phase-38's deployment ordering. Verified stable across three consecutive runs.

**Acceptance:** every run is traceable with cost and outcome; the cost dashboard shows per-project
spend and headroom and admins see global spend; caps are enforced visibly (warning → halt with a
clear message) and logs never contain secrets. Covered by `test_run_tracing.py` (11),
`test_log_redaction.py` (29), `test_cost_api.py` (12) and `CostPanel.test.tsx` (10).

**Verification:** ruff/black/mypy clean; backend **837 passed** (+51) with the 8 known pre-existing
env-only failures unchanged; frontend lint/typecheck/format clean, **220 passed** (+10), build green.
Live-verified **10/10** against real MongoDB — the plan's three manual steps, automated: costed work
produced a closed, correlated run (₹15.00 / 28,000 tokens, 1 tool call); the accidental
`extra={"api_key": ...}` was redacted **while the token count survived**; spend crossed 85% of a ₹100
cap and emitted `budget.warning` with ₹15.00 headroom *before* halting; going over halted with
`Budget cap reached (project: ₹125.00 of ₹100.00)` and the panel showed an honest **negative**
headroom; `/admin/cost` refused a non-admin with 403; and a final sweep of the whole 1,996-byte log
stream found the secret nowhere.

**Deferred (as scoped):** the security/sandbox hardening pass is phase-47 and resilience is
phase-48. The admin *UI* for global spend lands with phase-52 — the endpoint it will read is here.
