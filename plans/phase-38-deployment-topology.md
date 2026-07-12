# Phase 38 — Visual deployment topology (React Flow)

## Title & ID

- **ID:** `phase-38`
- **Title:** Visual deployment topology (React Flow)
- **Epic:** Epic 7 — Infra Analysis & Deployment
- **Milestone:** M4

## Goal

An n8n-style live component map (FE/BE/DB nodes + edges) with live status/health, per-node logs, URLs, and redeploy controls.

## Context

The visual, beginner-friendly infra surface of D11 (secondary differentiator, D1). Consumes `deploy.status` events (`phase-37`) and the `topology_snapshot` to show a real, live picture of the deployment.

## Depends on

- **Phase IDs:** `phase-05`, `phase-37`
- **External:** —

### Prerequisite check

- `phase-37` ✅ (deploy events + `Deployment` record), `phase-05` ✅ (WS).

## Detailed tasks

1. **Deploy feature (`src/features/deploy/`).** `@xyflow/react` graph: nodes **FE (Vercel)**, **BE (Render)**, **DB (Mongo/Atlas)**; edges FE→BE→DB; node status pills (deploying/healthy/degraded/failed) driven by `deploy.status`.
2. **Node details.** Click a node → drawer with URL (open/copy), env summary (secret-safe), health, and a **logs** stream (from the adapter logs via `phase-35/37`).
3. **Controls.** Deploy / **Redeploy** (per node or whole), mode toggle (seamless/BYO), cancel; disabled with a reason when the build prereq is unmet.
4. **Wiring.** Mount as the Deploy stage panel; subscribe to `deploy.status`; hydrate from the latest `Deployment`.

## Out of scope for this phase

- Orchestration logic (`phase-37`), live-URL validation (`Epic 8`). Visualization + controls only.

## Acceptance criteria

- Nodes reflect **live status** as a deploy progresses (events drive the graph).
- Per-node **logs** and **URLs** are viewable; env shown secret-safe.
- **Redeploy** works from the graph; controls disabled with a clear reason when prereqs are unmet.

## Tests to write

- `frontend/src/features/deploy/Topology.test.tsx` — renders FE/BE/DB; status updates from mock `deploy.status`.
- `frontend/src/features/deploy/NodeDrawer.test.tsx` — URL/logs/env render; redeploy calls API (mock).

## Manual verification steps

1. Deploy → watch nodes transition deploying→healthy in order (BE→FE); DB node healthy.
2. Open the BE node → logs stream; copy the URL.
3. Redeploy FE from the graph → node cycles and recovers.

## Rollback / cleanup notes

- UI-only. Reversible.

## Design notes

- The graph hydrates from the persisted `topology_snapshot` on load and then follows live events — so reopening the workspace shows the true last-known topology immediately.

## Status

⬜ todo
