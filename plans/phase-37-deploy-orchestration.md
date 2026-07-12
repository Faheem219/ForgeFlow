# Phase 37 — Deploy orchestration

## Title & ID

- **ID:** `phase-37`
- **Title:** Deploy orchestration
- **Epic:** Epic 7 — Infra Analysis & Deployment
- **Milestone:** M4

## Goal

End-to-end deploy in one action: build artifacts → deploy BE + FE in the right order → wire env/DB (FE→BE URL, BE→DB URI) → health-check → record a `Deployment` with status events.

## Context

Turns adapters (`phase-35`) + provisioning (`phase-36`) + plan (`phase-33`) into "one action → healthy live deployment" (D11). Enforces the only two hard prereqs (deploy⇐build) from §8. Delivers the deployed URL — the M4 milestone.

## Depends on

- **Phase IDs:** `phase-35`, `phase-36`
- **External:** deploy creds (platform/BYO); DB (Atlas/BYO).

### Prerequisite check

- A **passing build** exists (state machine hard prereq); `phase-35` ✅ adapters, `phase-36` ✅ DB URI, `phase-33` ✅ infra plan.

## Detailed tasks

1. **Orchestrator (`app/deploy/orchestrate.py`).** From the `infra_plan`:
   - Build the FE (produce static output) + prepare the BE for Render.
   - **Provision/resolve DB** (`phase-36`) → `MONGODB_URI`.
   - **Deploy BE→Render** with `MONGODB_URI` → obtain BE URL.
   - **Deploy FE→Vercel** with `VITE_API_BASE_URL=<BE URL>` → obtain FE URL.
   - **Health-check** both (retry/backoff); mark degraded on failure.
   - Record a `Deployment` `{mode, fe_target, be_target, db_target, urls, status, topology_snapshot, logs_ref}` (§6).
2. **Ordering + wiring.** BE before FE (FE needs the BE URL); env wiring both directions; idempotent re-deploy (new `Deployment` record/version).
3. **Events.** `deploy.status` per step (build/db/be/fe/health) for the topology UI (`phase-38`).
4. **Handler (`app/orchestrator/stages/deploy.py`).** Deploy stage: `proceed` runs orchestration; `skip`/`refine` per D12; blocks without a build.
5. **Failure handling.** Partial failures reported clearly; safe retry; degrade gracefully (risk §12, resilience `phase-48`).

## Out of scope for this phase

- Topology **UI** (`phase-38`), live-URL validation (`Epic 8`). Orchestration + records + events here.

## Acceptance criteria

- One action takes a **passing build** to a **healthy live deployment** with FE + BE URLs, DB wired, in both modes.
- Correct order (BE→FE) and env wiring (FE→BE URL, BE→DB URI).
- `deploy.status` events stream each step; a `Deployment` record is persisted with URLs + status.
- Deploy is blocked (clear message) if no build exists.

## Tests to write

- `backend/tests/deploy/test_orchestrate_happy.py` — full sequence with mocked adapters/DB → healthy `Deployment` + URLs.
- `backend/tests/deploy/test_orchestrate_env_wiring.py` — FE gets BE URL; BE gets DB URI.
- `backend/tests/deploy/test_orchestrate_prereq.py` — no build → blocked.
- `backend/tests/deploy/test_orchestrate_partial_failure.py` — BE ok, FE fails → status degraded, clear report, safe retry.

## Manual verification steps

1. With a passing build → Deploy → watch step events → FE+BE URLs returned; open FE → talks to BE → persists to DB.
2. Re-deploy → new `Deployment` version; URLs updated.
3. Attempt deploy without a build → blocked with reason.

## Rollback / cleanup notes

- `destroy` FE/BE via adapters; optionally drop the platform DB (confirm — data loss). Live resources must be cleaned to avoid provider spend. Reversible via provider APIs.

## Design notes

- The `topology_snapshot` captured here is exactly what the React Flow view (`phase-38`) renders, so the graph reflects the real deploy, not a mock.

## Status

⬜ todo
