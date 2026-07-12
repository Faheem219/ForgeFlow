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

⬜ todo
