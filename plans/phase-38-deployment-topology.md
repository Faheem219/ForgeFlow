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

✅ done — the Deploy stage is now a **live component map**: FE/BE/DB nodes wired FE→BE→DB, each
node's status driven by the `deploy.status` events phase-37 emits, with per-node URLs, env and logs
and a redeploy control. This is the visual, beginner-friendly infra surface of D11.

- `app/deploy/deploy_router.py` — the read API the graph hydrates from (a reload has no events to
  replay): `GET /projects/{id}/deploy/latest` (the newest `Deployment` — status, URLs, topology) and
  `GET …/deploy/latest/logs` (the step log). Read-only **by design**: deploying stays on the
  conductor (`POST /intent`, `stage=deploy`), so the deploy⇐build prereq (§8) is enforced in exactly
  one place. Ownership is checked through the project, so a foreign deployment is a `404`.
- `features/deploy/topologyModel.ts` — the pure view-model (graph build, the `deploy.status` fold,
  env-key derivation, log filtering), so the graph's whole behaviour is testable without mounting
  React Flow. Before the first deploy it falls back to the fixed-stack shape, so the map shows what
  a deploy *will* build.
- `features/deploy/Topology.tsx` + `TopologyNode.tsx` — the `@xyflow/react` graph (added as a
  dependency this phase); edges animate while either end is deploying.
- `features/deploy/NodeDrawer.tsx` — per-node URL (open/copy), **env keys**, health/error and that
  node's log lines. The env list is secret-safe *by construction*: a `Deployment` stores no values,
  so there is nothing in the payload that could leak a token or connection string (§7).
- `features/deploy/DeployPanel.tsx` — mounted as the Deploy stage panel (lazy, so React Flow lands
  in its own chunk). Hydrates from the last `Deployment`, then follows live events; mode toggle
  (seamless/BYO) rides on `intent.payload.mode`; Deploy/Redeploy is disabled **with the reason**
  when the build prereq is unmet, and a terminal `health` event refetches the record.

**Acceptance:** nodes reflect live status as a deploy progresses (events drive the graph); per-node
logs and URLs are viewable and env is shown secret-safe; redeploy works from the graph and from a
node, and controls are disabled with a clear reason when the prereq is unmet. Covered by
`Topology.test.tsx` (7: renders FE/BE/DB, hydrates from a snapshot, node selection, deploying→healthy
from mocked `deploy.status`, a failed node without losing healthy ones, prereq blocking, mode-carrying
deploy), `NodeDrawer.test.tsx` (6: URL + copy, env keys with no values, node-filtered logs, redeploy,
disabled-with-reason, failed-node error), `topologyModel.test.ts` (10: hydration, fold, edges, env,
log filtering) and `tests/deploy/test_deploy_router.py` (6: null before first deploy, newest wins,
secret-safety, logs, empty logs, cross-user 404).

**Verification:** frontend `lint`/`typecheck`/`format` clean and **141 passed** (30 files, +23 this
phase, no act warnings); `pnpm build` succeeds with the panel code-split into its own chunk. Backend
ruff/black clean, **579 passed** (+6) with the 8 known pre-existing env-only failures unchanged (7
Docker-daemon repair tests + the anthropic SDK-auth suggest test) and the one pre-existing mypy
failure confined to `tests/agents/test_codegen_nonlinear.py`. Live-verified 6/6 against real MongoDB
end-to-end: a real phase-37 deploy → `GET /deploy/latest` → the exact nodes/edges the UI draws →
per-node logs → the DB URI present in the *deploy env* but absent from every API payload → a
re-deploy re-hydrating the graph from the new record.

**Deferred (as scoped):** live-URL validation is Epic 8. **Per-node redeploy re-runs the whole
pipeline** rather than one target: the SPA bakes `VITE_API_BASE_URL` at build time, so a frontend
redeploy needs the backend URL anyway, and target-scoped orchestration is phase-37 logic that this
phase lists as out of scope — the drawer says so plainly instead of faking granularity. **Cancel** is
likewise not wired: phase-37 exposes no cancellation seam, and adding one is orchestration work
(phase-48, resilience). Acceptance criteria do not require either.
