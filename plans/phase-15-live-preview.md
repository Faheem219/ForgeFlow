# Phase 15 — Live preview

## Title & ID

- **ID:** `phase-15`
- **Title:** Live preview
- **Epic:** Epic 2 — Sandbox & Web IDE
- **Milestone:** M1

## Goal

Run the generated app in its sandbox (Vite FE + Express BE) and preview it in-app via a reverse-proxied URL, with a refresh control and live logs.

## Context

Closes the "see it running" loop (D5). Requires a **restricted** preview network (FE↔BE reachable, host still isolated) layered onto the `network_mode=none` default from `phase-11`. Reverse proxy per §4 (Caddy locally / prod subdomains).

## Depends on

- **Phase IDs:** `phase-11`, `phase-13`
- **External:** proxy (Caddy) from `phase-01` infra; `PREVIEW_BASE_DOMAIN` for prod.

### Prerequisite check

- `phase-11` ✅ (sandbox), `phase-13` ✅ (can start dev servers via exec); proxy reachable.

## Detailed tasks

1. **Preview service (`app/sandbox/preview.py`).** Start FE (`vite` dev/preview) and BE (`node`/`tsx` Express) inside the container via `phase-13` exec; track their ports; health-poll both; stop/restart; stream `terminal.output` logs tagged FE/BE.
2. **Networking.** Attach the sandbox to a **dedicated restricted docker network** for preview (FE can reach BE; **no host network**); map/publish only the preview ports needed, or route exclusively through the proxy. Never expose the host.
3. **Reverse proxy.** Local: port-map or Caddy route `→ container:port`. Prod: Caddy/Traefik wildcard subdomain per project (`<project>.PREVIEW_BASE_DOMAIN`). A `preview_url` resolver returns the correct URL per environment.
4. **API + events.** `POST /projects/{id}/preview/(start|stop|restart)`, `GET …/preview/status` (`{fe_url, be_url, fe_status, be_status}`); emit `preview.status`. Ownership-checked.
5. **Frontend preview pane (`src/features/ide/Preview.tsx`).** Iframe to `fe_url`; refresh button; status pills; a logs drawer (FE/BE tabs) bound to the log stream; "open in new tab".

## Out of scope for this phase

- The app **content** to preview (skeleton `phase-22`, codegen `phase-23`) — verify with a minimal scaffold/sample. Production deployment (`Epic 7`). Live-URL validation (`Epic 8`).

## Acceptance criteria

- A scaffolded app renders in the preview iframe; the FE can call the BE (BE reachable from FE within the restricted network).
- Start/stop/restart work; status + logs stream to the UI.
- Host isolation preserved: the sandbox still has **no host network**; only preview routing is exposed.

## Tests to write

- `backend/tests/sandbox/test_preview_lifecycle.py` — start→status healthy→stop (integration behind docker marker; unit with fakes).
- `backend/tests/sandbox/test_preview_network.py` — created network config keeps host isolated; FE↔BE allowed.
- `frontend/src/features/ide/Preview.test.tsx` — iframe src + refresh + status pills (mock).

## Manual verification steps

1. Place a minimal FE+BE in a sandbox → `preview/start` → open the pane → app renders; a FE→BE fetch succeeds.
2. Restart → recovers; Stop → pane shows stopped; logs drawer shows FE/BE output.

## Rollback / cleanup notes

- Stop preview processes on sandbox stop/destroy; remove the preview network if unused. Reversible.

## Design notes

- Preview uses a **dedicated restricted network**, an intentional, minimal relaxation of `phase-11`'s `network_mode=none` — never the host network; documented for the `phase-47` hardening review.

## Status

✅ done — **Closes Epic 2 and milestone M1.** `app/sandbox/preview.py` `PreviewService`:
`start`/`stop`/`restart`/`status` run the skeleton's BE then FE inside the sandbox via phase-13
exec, stream their output as `terminal.output` tagged `source=preview` + `process=frontend|backend`,
health-poll both (fail-fast if one exits), and report `{fe_url, be_url, fe_status, be_status}` with a
new `preview.status` event. Health probes run **inside the sandbox** (`curl` on loopback) rather than
from the control plane, so they need no host access and work regardless of networking. Injects the
phase-22 env contract: BE gets `PORT`/`MONGODB_URI` (scoped to the project's own `app_db_name`), FE
gets `VITE_API_BASE_URL` = the BE's URL. Routes `POST …/preview/(start|stop|restart)` +
`GET …/preview/status`, ownership-checked. Frontend `Preview.tsx`: iframe (sandboxed, never
`allow-same-origin`), refresh (remount — the only cross-origin reload), status pills, start/stop/
restart, open-in-new-tab, and an FE/BE logs drawer fed by the realtime stream; mounted beside the IDE
in the Build stage, lazy-loaded.

**Networking (the one deliberate relaxation of phase-11's `network_mode=none`):** a *shared*
`forgeflow-preview` docker network created with `internal: true` — sandboxes joined to it can be
reached by the proxy but have **no route to the host or the internet**. The sandbox joins on start
and is detached on stop; the default posture for ordinary sandboxes is unchanged (asserted by test).
Caddy straddles the default + preview networks and routes by *container name*, so no config
regeneration is needed per project: `<id>.preview.localhost` → FE, `<id>.api.preview.localhost` → BE
(prod: the same shape under `PREVIEW_BASE_DOMAIN`).

Verified: ruff/black/mypy + eslint/prettier/tsc clean; **backend 238 passed** (19 new) **+ frontend
76 passed** (9 new, stable over 3 consecutive full runs); build OK; `docker compose config` renders
`forgeflow-preview` as `internal: true` with the proxy on both networks. **Live through the real HTTP
API — 17/17**: stopped→start brings FE+BE up healthy, correct proxied URLs, the FE serves the page the
iframe loads, the BE is reachable, **FE got `VITE_API_BASE_URL`=be_url and BE got a `MONGODB_URI`
scoped to its own project DB**, logs stream tagged FE/BE with `preview.status` events, restart
recovers, stop really kills the dev servers, and foreign-project/unauth are rejected.

**Trade-offs / notes for the phase-47 hardening review:**
- The preview network is **shared, not per-project**: sandboxes on it can reach each other's dev
  servers. Per-project networks would be stricter but Caddy cannot join them dynamically from static
  compose. The plan's own wording ("a dedicated restricted docker network") matches this; flagged.
- `internal: true` also means the sandbox has **no registry access**, so `pnpm install` cannot run
  while attached. Dependency installation is an agent-tools/codegen concern (phases 21/23) and will
  need its own answer.
- BE↔Mongo reachability is likewise out of scope here — the env contract is honored, real per-project
  DB provisioning lands in **phase-36**.

**Deferred (scoped):** the app *content* is the skeleton (phase-22) / codegen (phase-23) — verified
with a minimal stand-in FE+BE as the plan directs. The docker-network attach/detach path and the
Caddyfile's syntax could not be executed here (no Colima); both are covered by pure spec assertions
and rendered-compose validation, with the identical service logic proven via `LocalRuntime`.
