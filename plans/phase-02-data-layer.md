# Phase 02 — Control-plane data layer

## Title & ID

- **ID:** `phase-02`
- **Title:** Control-plane data layer
- **Epic:** Epic 0 — Foundations & Scaffolding
- **Milestone:** M1

## Goal

Connect to MongoDB and implement the full Section 6 domain model as Beanie documents with repository classes, indexes, and a seed/migration script — the persistence backbone for every later phase.

## Context

Implements `IMPLEMENTATION_PLAN.md` §6 (domain model) and D8 (single Atlas cluster; control-plane metadata in `forgeflow_meta`; one DB per user-project for generated-app data). Adds one collection beyond §6 — `PlatformSetting` — to back the admin-configurable settings layer (`admin panel > env > default`, resolved at `phase-51`).

## Depends on

- **Phase IDs:** `phase-01`
- **External:** reachable MongoDB (`MONGODB_URI`), `FORGEFLOW_META_DB=forgeflow_meta`.

### Prerequisite check

- `phase-01` ✅; `make dev` boots Mongo.
- Config resolver returns `MONGODB_URI` and `FORGEFLOW_META_DB`.

## Detailed tasks

1. **Mongo client (`app/db/mongo.py`).** Motor client + Beanie `init_beanie` over `forgeflow_meta`; lifespan hook wired into `create_app()`; helper to get a **per-project app DB** handle by `app_db_name` (isolation for D8) — used by later data-browser/provisioning phases.
2. **Beanie documents (`app/db/models/`)** — one module per entity, all fields typed:
   - `user.py` **User** — `email` (unique idx), `hashed_password`, `role` (`user|admin`, default `user`), `settings` (dict), `created_at`.
   - `credential.py` **Credential** — `user_id` (idx), `kind` (`vercel|render|mongo_uri|stitch|figma`), `encrypted_secret`, `scope` (`platform|byo`), `created_at`. (Value encrypted; encryption itself lands in `phase-34`.)
   - `project.py` **Project** — `user_id` (idx), `name`, `current_stage`, `status`, `stack` (`fixed-mern-ts`), `sandbox_id?`, `app_db_name`, `created_at`, `updated_at`. Compound idx `(user_id, created_at)`.
   - `stage_state.py` **StageState** — `project_id` (idx), `stage`, `status` (`empty|in_progress|awaiting_user|complete|skipped`), `artifacts` (refs), `updated_at`. Unique idx `(project_id, stage)`.
   - `message.py` **Message** — `project_id` (idx), `stage?`, `role`, `content`, `artifacts`, `token_usage`, `created_at`.
   - `artifact.py` **Artifact** — `project_id` (idx), `stage`, `type` (`design|code_change|requirement|test|test_result|repair_attempt|deployment|infra_plan`), `version` (int), `ref`, `meta`, `created_at`. Compound idx `(project_id, stage, type, version)`.
   - `requirement.py` **RequirementSpec** — `project_id`, `features[]` = `{name, description, inputs[], expected_behaviors[], acceptance_criteria[]}`, `version`.
   - `test_suite.py` **TestSuite** — `project_id`, `kind` (`unit|e2e`), `files[]`, `generated_from[]`, `version`.
   - `test_run.py` **TestRun** — `project_id`, `suite_refs[]`, `results[]`, `failures[]`, `stdout_ref`, `created_at`.
   - `repair_attempt.py` **RepairAttempt** — `project_id`, `run_id`, `iteration`, `target_files[]`, `diff_ref`, `resulting_run_id`, `outcome` (`fixed|no_progress|regressed`), `created_at`.
   - `deployment.py` **Deployment** — `project_id`, `mode` (`seamless|byo`), `fe_target`, `be_target`, `db_target`, `urls`, `status`, `topology_snapshot`, `logs_ref`, `created_at`.
   - `run.py` **Run/Trace** — `project_id`, `kind`, `cost` (`{tokens, inr}`), `started_at`, `finished_at`, `events_ref`.
   - `platform_setting.py` **PlatformSetting** — `key` (unique idx), `value` (JSON), `category` (`models|providers|deploy|sandbox|budget|feature_flags`), `updated_by`, `updated_at`. Backs the admin config layer.
3. **Repositories (`app/db/repos/`).** One repo per aggregate with typed async CRUD + query helpers (e.g., `ProjectRepo.list_for_user`, `ArtifactRepo.latest_version`, `StageStateRepo.get_or_create`). Repos are the only DB access surface for services.
4. **Indexes + init.** Ensure indexes on Beanie init; idempotent.
5. **Seed/migration (`backend/scripts/seed.py`).** Create indexes, optionally seed a dev admin user (via `phase-03` hashing later) and default `PlatformSetting` rows mirroring env defaults.
6. **Test fixtures (`backend/tests/conftest.py`).** Spin a disposable test DB (unique name per run) + teardown.

## Out of scope for this phase

- Secret **encryption** implementation (`phase-34`) — store the field, don't encrypt yet. Auth/hashing (`phase-03`). The admin config **resolver wiring + API** (`phase-51`). Per-project DB **provisioning** (`phase-36`).

## Acceptance criteria

- All §6 entities + `PlatformSetting` exist as Beanie docs with the specified indexes.
- Repository CRUD unit tests pass against a disposable test DB.
- `ArtifactRepo` versions (never overwrites) — inserting a new version increments and both are retrievable.
- `StageStateRepo.get_or_create` yields exactly one state per `(project_id, stage)`.

## Tests to write

- `backend/tests/db/test_repos_crud.py` — CRUD for each repo.
- `backend/tests/db/test_artifact_versioning.py` — versioning + `latest_version`.
- `backend/tests/db/test_indexes.py` — expected indexes present; uniqueness enforced (`User.email`, `StageState (project_id,stage)`, `PlatformSetting.key`).

## Manual verification steps

1. `uv run python backend/scripts/seed.py` → indexes created, no error.
2. In a `mongosh` shell against `forgeflow_meta`: `db.getCollectionNames()` shows all collections; `db.users.getIndexes()` shows the unique email index.

## Rollback / cleanup notes

- Drop the `forgeflow_meta` DB (and any test DBs) to fully reset. Reversible.

## Design notes

- **`role` on User** and **`PlatformSetting`** are added now (ahead of `phase-51`) so the domain model is coherent from the start and admin RBAC/config have their storage ready. No behavior yet — pure schema.

## Status

✅ done — All §6 entities + `PlatformSetting` implemented as Beanie documents with the specified
indexes; `BaseRepo` + per-aggregate repos (incl. `ProjectRepo.list_for_user`,
`ArtifactRepo.latest_version`/`create_version`, `StageStateRepo.get_or_create`,
`PlatformSettingRepo.upsert`); Mongo client + best-effort lifespan wiring in `create_app()`;
seed script (`make seed`) + disposable-DB test fixture. Verified against a real ephemeral
MongoDB (mongod 7.0.11): `make lint` clean and `make test` = **backend 26 passed** (incl. DB
integration: CRUD, artifact versioning, unique indexes for email / (project,stage) / setting key)
**+ frontend 3 passed**; seed created 7 default platform settings; CI backend job now runs a
`mongo:7` service. **Adaptation (flagged):** pinned `beanie<2` to keep the documented **Motor +
Beanie** stack (§4) — Beanie 2.x switched to pymongo-async. Deferred: dev-admin-user seeding
(needs password hashing, phase-03) and secret **encryption** (phase-34).
