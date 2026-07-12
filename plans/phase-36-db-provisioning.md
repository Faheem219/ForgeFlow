# Phase 36 — DB provisioning

## Title & ID

- **ID:** `phase-36`
- **Title:** DB provisioning
- **Epic:** Epic 7 — Infra Analysis & Deployment
- **Milestone:** M4

## Goal

Give each project an isolated database (a per-project DB in the shared Atlas cluster, or a user-supplied URI) and inject its `MONGODB_URI` into the generated app for both preview and deploy.

## Context

Implements D8 (one DB per user-project; users may supply their own URI; per-project isolation). The injected URI is what makes the generated app actually persist data in preview (`phase-15`) and prod (`phase-37`).

## Depends on

- **Phase IDs:** `phase-02`, `phase-34`
- **External:** shared Atlas cluster (platform) or BYO URI.

### Prerequisite check

- `phase-02` ✅ (`Project.app_db_name`), `phase-34` ✅ (store BYO URI in the vault). Atlas reachable (platform mode).

## Detailed tasks

1. **Provisioning service (`app/deploy/db_provision.py`).**
   - **Platform mode:** assign/create a per-project database (`app_db_name`) in the shared Atlas cluster; ensure isolation (distinct DB per project; least-privilege where feasible).
   - **BYO mode:** accept + validate a user-supplied `MONGODB_URI` (from the vault), don't manage its lifecycle.
   - `get_app_mongodb_uri(project) -> str` returning the correct URI per mode (secret-safe).
2. **Injection.** Provide the URI to: preview (`phase-15` BE env) and deploy (`phase-37` Render env). Never expose it to the FE or client.
3. **Isolation guarantees.** A project can only reach its own DB; document the isolation model; add a guard used by the data browser (`Epic 9`).
4. **Lifecycle.** Assign on first need; optional teardown on project delete (platform mode only; never drop BYO).

## Out of scope for this phase

- The data browser (`Epic 9`), deploy orchestration (`phase-37`). Provisioning + injection + isolation only.

## Acceptance criteria

- Each project gets an **isolated** DB (or a validated BYO URI); two projects never share a database.
- The generated app connects using the injected URI in **preview** and **prod**.
- BYO URI is honored and never lifecycle-managed by ForgeFlow.
- URI is treated as a secret (never logged/returned to client).

## Tests to write

- `backend/tests/deploy/test_db_provision_platform.py` — distinct `app_db_name` per project; isolation.
- `backend/tests/deploy/test_db_provision_byo.py` — BYO URI validated + used; not managed.
- `backend/tests/deploy/test_uri_injection.py` — correct URI surfaced to preview/deploy env; secret-safe.

## Manual verification steps

1. New project → provision → BE in preview connects + persists to its own DB.
2. Provide a BYO URI → used in preview/prod; ForgeFlow doesn't alter it.
3. Two projects → two DBs; cross access impossible.

## Rollback / cleanup notes

- Platform DBs may be dropped on project delete (confirm first — **drops data**). **Never** drop BYO databases. Partially irreversible (data loss) — guard with confirmation.

## Design notes

- Per-project **database** (not just collection prefixing) gives the strongest isolation on M0/shared clusters and matches D8's "one database per user-project."

## Status

⬜ todo
