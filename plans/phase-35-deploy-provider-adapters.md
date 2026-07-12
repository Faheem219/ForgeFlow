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

⬜ todo
