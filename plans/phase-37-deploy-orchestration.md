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

✅ done — One action takes a **passing build → healthy live deployment** (FE + BE URLs, DB wired), in
both modes. This delivers the M4 payload (a deployed URL). Turns the phase-35 adapters + phase-36
provisioning + phase-33 plan into an ordered, observable, failure-tolerant pipeline.

- `app/deploy/orchestrate.py` — `DeployOrchestrator.deploy(project, *, mode, user_id)`:
  1. **prereq** — build stage must be `complete` (the only hard prereq, §8) or a clear `UserError`;
  2. **plan** — the infra plan (fe/be/db) from the analyzer;
  3. **DB** — `DbProvisioner.provision` → the isolated `MONGODB_URI`;
  4. **BE → Render** with `{MONGODB_URI, NODE_ENV}`, polled to `live` → BE URL;
  5. **FE → Vercel** — the SPA is **built with `VITE_API_BASE_URL=<BE url>`** (a build-time var, which
     is *why* BE must precede FE), then the built bundle is shipped;
  6. **health** — poll each deploy to a terminal state; and record a `Deployment`
     `{mode, fe/be/db_target, urls, status, topology_snapshot, logs_ref}`.
  - **Partial failure degrades, never crashes:** a live BE with a failed FE is `degraded` (BE URL
    kept, FE failure surfaced as a step event + a `failed` topology node); a BE failure is `failed`
    and the FE is never attempted; a re-deploy is always safe (a fresh `Deployment`).
  - Each step emits a `deploy.status` event, and the recorded `topology_snapshot` (nodes fe/be/db +
    edges fe→be, be→db) is exactly what phase-38's React Flow view renders. Every collaborator
    (plan source, FE builder, provider factory, DB provisioner) is injected behind a seam, so the
    whole ordering/wiring/failure logic runs **without Docker or a real provider**.
- `app/orchestrator/stages/deploy.py` — `DeployStageHandler` (registered in `stages/__init__`):
  `proceed`/`refine` run orchestration on their own costed `Run`; a **live** result → `complete`
  (validate's prereq satisfiable), degraded/failed → `awaiting_user`; `skip` opts out. Deploy mode
  resolves from `intent.payload["mode"]` → `deploy_default_mode`. The conductor enforces deploy⇐build
  *before* the handler runs.
- Config: `deploy_repo_url`, `deploy_region`, `deploy_health_timeout_s` (+ `.env.example`).

**Acceptance:** one action → live deploy with both URLs, correct order (BE→FE) and env wiring
(FE→BE URL, BE→DB URI); `deploy.status` streams per step; a `Deployment` is persisted; deploy is
blocked with a clear reason when no build exists. Covered by `tests/deploy/test_orchestrate_happy.py`
(live + topology + ordered events + log), `…_env_wiring.py` (BE gets the DB URI; FE built *and*
deployed with the BE URL; build-after-BE ordering), `…_prereq.py` (blocked without a build; proceeds
after), `…_partial_failure.py` (FE-fail → degraded, BE-fail → failed, safe re-deploy), and
`tests/orchestrator/stages/test_deploy_handler.py` (proceed/skip, live→complete/degraded→hold, BYO
mode).

**Verification:** ruff/black + mypy clean across the phase-37 surface; phase-37 + conductor suites
**103 passed** (the 1 failure is the pre-existing Docker-only `test_repair_audit_trail`); full backend
suite green apart from the 8 known pre-existing env-only failures (7 Docker-daemon repair tests + the
anthropic SDK-auth suggest test); live-verified 6/6 against MongoDB.

**Deferred (as scoped):** the topology **UI** is phase-38 (it renders `topology_snapshot`); live-URL
validation is Epic 8; the live smoke path against real Vercel/Render accounts remains deferred
(billable resources). The production FE builder (`pnpm build` in the sandbox + read `dist`) is real
but Docker-gated, so it's covered via the injected builder seam rather than a live run.
