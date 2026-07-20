# Phase 35 — Deploy provider adapters (Vercel + Render)

## Title & ID

- **ID:** `phase-35`
- **Title:** Deploy provider adapters (Vercel + Render)
- **Epic:** Epic 7 — Infra Analysis & Deployment
- **Milestone:** M4

## Goal

Adapters to deploy FE→Vercel (SPA) and BE→Render (Node web service) in both **seamless** (platform creds) and **BYO** modes, behind a common `DeployProvider` interface.

## Context

Implements D11 dual-mode deploy. **BE never goes to Vercel** (D11/risk §12) — Render hosts the persistent Node service. Adapters expose deploy + env + status/logs so the orchestrator (`phase-37`) and topology (`phase-38`) can drive and observe them.

## Depends on

- **Phase IDs:** `phase-33`, `phase-34`
- **External:** Vercel token + Render API key (platform and/or BYO via vault).

### Prerequisite check

- `phase-33` ✅ (infra plan), `phase-34` ✅ (vault for creds). `VERCEL_TOKEN`/`RENDER_API_KEY` available (platform) or BYO present.

## Detailed tasks

1. **Interface (`app/deploy/providers/base.py`).** `DeployProvider`: `deploy(artifact, env, mode) -> {id, url, status}`, `set_env(...)`, `status(id)`, `logs(id)`, `destroy(id)`.
2. **Vercel adapter (`app/deploy/providers/vercel.py`).** Deploy the built **SPA** (static output from the FE build), set env (e.g., `VITE_API_BASE_URL`), fetch status/logs, get the live URL. Creds via vault (BYO-aware). **FE only.**
3. **Render adapter (`app/deploy/providers/render.py`).** Create/deploy a **Node web service** for the BE (start cmd, port, env incl. `MONGODB_URI`), status/logs, live URL. Creds via vault. **BE only — never Vercel.**
4. **Modes.** `seamless` (platform creds) and `byo` (user creds) selected per `Deployment.mode`; resolution via the vault (`phase-34`).
5. **Errors/resilience.** Timeouts, retries, structured `ProviderError`; partial-failure reporting for the orchestrator.

## Out of scope for this phase

- Orchestration/ordering/health-wiring (`phase-37`), DB provisioning (`phase-36`), topology UI (`phase-38`). Just the adapters.

## Acceptance criteria

- A built app deploys **FE→Vercel** and **BE→Render** and returns live URLs, in **both** modes (mocked integration + a live smoke path).
- BE is **never** targeted to Vercel (enforced/typed).
- Env vars set correctly per target; status + logs retrievable.

## Tests to write

- `backend/tests/deploy/test_vercel_adapter.py` — deploy SPA, set env, status/logs (mocked HTTP).
- `backend/tests/deploy/test_render_adapter.py` — deploy Node service, env, status/logs (mocked HTTP).
- `backend/tests/deploy/test_modes.py` — seamless vs BYO cred resolution.
- `backend/tests/deploy/test_be_not_on_vercel.py` — BE→Vercel path is impossible.

## Manual verification steps

1. (Mocked) deploy FE+BE → URLs returned; env visible on both targets.
2. Live smoke (platform creds): deploy the skeleton FE to Vercel + BE to Render → both reachable.

## Rollback / cleanup notes

- `destroy(id)` per target; remove test deployments. Live deployments incur provider-side resources — clean them up. Reversible via provider APIs.

## Design notes

- The `DeployProvider` interface mirrors `DesignProvider`'s pluggability so future targets (if ever) slot in without touching the orchestrator — though scope forbids adding more now.

## Status

✅ done

**Implementation notes**

- **Interface** — [providers/base.py](../backend/app/deploy/providers/base.py): `DeployProvider` Protocol (`deploy`/`set_env`/`status`/`logs`/`destroy`), plus `DeploySpec`, `DeployRef`, `DeployResult`, `DeployState`, `LogLine` and a `BaseDeployProvider` holding the shared credential resolution, HTTP calling, retry and error classification.
- **BE-never-on-Vercel is structural, not conventional.** Each provider declares a single class-level `target`; `_check_target` rejects a mismatched spec **before any network call**, and `provider_for()` is the only lookup (fe→Vercel, be→Render, no way to add a second). Tested both directions plus the registry.
- **Vercel** ([vercel.py](../backend/app/deploy/providers/vercel.py)) — FE only. Uploads the *built* SPA inline (`POST /v13/deployments`), so Vercel never needs repo access and what ships is exactly what the repair loop validated. Env vars are **project**-scoped (`/v10/projects/{p}/env`, upsert) while status/logs/destroy are **deployment**-scoped — hence `DeployRef` carrying both id and project. Prefers the production alias over the per-deploy host.
- **Render** ([render.py](../backend/app/deploy/providers/render.py)) — BE only. Creates a persistent Node `web_service` with build/start commands, then explicitly triggers a deploy so a build is always in flight. `DeployRef.id` is the **service** id (the durable object every later call keys off).
- **Modes (D11)** — `seamless` resolves the *platform* credential only (vault → config) and never silently spends a user's BYO token; `byo` requires that user's token and fails with an actionable auth error rather than falling back to the platform's. Both directions asserted.
- **Errors/resilience** — [providers/errors.py](../backend/app/deploy/providers/errors.py): `auth|config|quota|transient|fatal`, each with a `fallback_hint`. `detail` carries `{provider, kind, target}` — that `target` is what lets phase-37 report a *partial* deploy ("FE live, BE failed"). Bounded exponential backoff on transient failures; response bodies are never echoed into errors (they can contain env values).
- Config: `vercel_api_url`, `render_api_url`, `deploy_timeout_s`, `deploy_max_retries` added to `Settings` + `.env.example`.
- Tests use `httpx.MockTransport` rather than a mocked client, so the adapters' real URLs, methods, auth headers and JSON bodies must all be correct for a route to match: `test_vercel_adapter.py` (20), `test_render_adapter.py` (19), `test_modes.py` + `test_be_not_on_vercel.py` (19). Backend 456 passed (the 7 failures are the pre-existing Docker-daemon-dependent repair tests, identical at HEAD).

**Not done here (as scoped):** the live smoke path against real Vercel/Render accounts is deferred — it needs real credentials and creates billable provider resources. Everything else is covered by mocked integration. Orchestration/ordering is phase-37.
