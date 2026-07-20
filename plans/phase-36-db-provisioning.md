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

✅ done — Each project gets an **isolated database** and ForgeFlow injects its `MONGODB_URI` into the
generated app for preview (and, via the same helper, prod). Implements D8: one DB per user-project,
per-project isolation, BYO honored.

- `app/deploy/db_provision.py` — `DbProvisioner` with two modes:
  - **Platform:** a per-project *database* (`Project.app_db_name`, the globally-unique
    `forgeflow_app_<uuid>`) in the shared cluster. Distinct database per project = strongest
    isolation on a shared cluster (design note); MongoDB creates it lazily on first write, so
    "provisioning" is composing the correct URI (no admin API call). ForgeFlow-managed.
    Base precedence: **vault platform override > `APP_DB_CLUSTER_URI` > `MONGODB_URI`**.
  - **BYO:** a user-supplied `MONGODB_URI` from the vault (phase-34), validated + used **verbatim**
    and **never lifecycle-managed**. The lookup is fail-soft (a DB/vault hiccup → platform), so it
    can sit on the preview/deploy hot path.
  - `get_app_mongodb_uri(project)` returns the secret URI (env-injection only); `info(project)` is the
    secret-safe metadata (`mode`/`db_name`/`managed`, never the URI); `assert_owns_db` is the
    isolation guard the data browser (Epic 9) will use; `teardown(project, drop_data=…)` drops a
    **platform** DB only, and only with the explicit destructive opt-in — a BYO DB is never dropped.
- **URI composition** slots the per-project db name into a cluster base while preserving the query
  string and replacing any stray path (`mongodb+srv://…/?w=majority` → `…/forgeflow_app_x?w=majority`).
- **Injection** — preview's backend env (`app/sandbox/preview.py`) now sources `MONGODB_URI` from
  `DbProvisioner.get_app_mongodb_uri` (replacing the phase-15 placeholder), so BYO is honored in
  preview; phase-37's Render env will call the same helper.
- **Vault** — small `SecretVault.platform_secret(kind)` (fail-soft platform-scope read, no config
  fallback) so the app-DB cluster precedence is explicit. New config `app_db_cluster_uri`
  (+ `.env.example`).

**Acceptance:** two projects never share a database (distinct `app_db_name`); the app connects via the
injected URI in preview (verified by driving `PreviewService.start` with a recording runtime); a BYO
URI is honored and never managed; the URI is a secret (never logged — asserted). Covered by
`tests/deploy/test_db_provision_platform.py` (distinct DBs, platform metadata, cluster-base config,
isolation guard, teardown opt-in), `test_db_provision_byo.py` (verbatim use, unmanaged, malformed
rejected, never-dropped), and `test_uri_injection.py` (composition, secret-safety, preview env).

**Verification:** ruff/black + mypy clean across the phase-36 surface; phase-36 suite **16 passed** +
phase-15 preview regression **7 passed** (the injection change is transparent to the platform path)
against a live MongoDB; live-verified end-to-end. Full backend suite green apart from the one
pre-existing env-only `requirements_suggest` SDK-auth failure (unrelated).

**Deferred (as scoped):** Render prod env injection lands with deploy orchestration (phase-37), which
calls `get_app_mongodb_uri`; the data-browser consumption of `assert_owns_db`/`info` lands in Epic 9.
