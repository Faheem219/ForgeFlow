# Phase 41 — Per-project data API

## Title & ID

- **ID:** `phase-41`
- **Title:** Per-project data API
- **Epic:** Epic 9 — Project Data Browser
- **Milestone:** M5

## Goal

Scoped CRUD over a single project's database: list collections, read/query, create/update/delete — strictly isolated to the owning project's DB.

## Context

Delivers the "project-scoped CRUD data browser" of D8 (zero DB setup for the user). Isolation is the security-critical invariant: a project can reach **only** its own DB (never `forgeflow_meta`, never another project's DB).

## Depends on

- **Phase IDs:** `phase-36`
- **External:** —

### Prerequisite check

- `phase-36` ✅ (per-project DB + `get_app_mongodb_uri`); ownership/auth (`phase-03`).

## Detailed tasks

1. **Service (`app/data_browser/service.py`).** Using the project's app DB handle (`phase-02`/`phase-36`): `list_collections`, `find(collection, filter, sort, page, limit)`, `get(collection, id)`, `insert`, `update`, `delete`, `count`. **Never** accept an arbitrary DB name — always resolve from the project (isolation guard).
2. **Guardrails.** Reject access to `forgeflow_meta` and any non-owned DB; validate filters (no server-side JS/`$where`); cap page size + query time; sanitize/type inputs.
3. **Router (`app/data_browser/router.py`).** `GET /projects/{id}/data/collections`, `GET …/collections/{c}/docs?filter=&sort=&page=`, `GET/POST/PUT/DELETE …/collections/{c}/docs[/{docId}]`. Ownership-checked on every call.
4. **BYO awareness.** Works against BYO URIs too (read/write only what the URI grants).

## Out of scope for this phase

- The UI (`phase-42`). Aggregation pipelines / advanced querying (basic filter/sort only).

## Acceptance criteria

- CRUD works against the **owning project's** DB only.
- Cross-project and `forgeflow_meta` access are **impossible** (guarded + tested).
- Dangerous query operators are rejected; page size/time capped.

## Tests to write

- `backend/tests/data_browser/test_scoped_crud.py` — list/find/get/insert/update/delete on the project DB.
- `backend/tests/data_browser/test_isolation.py` — attempts to reach another project's DB / `forgeflow_meta` → rejected.
- `backend/tests/data_browser/test_query_guardrails.py` — `$where`/oversized queries rejected.

## Manual verification steps

1. Seed docs in a project's DB → `GET …/collections` + docs → visible.
2. Create/update/delete a doc via the API → reflected in the DB.
3. Try to point the API at another project's DB → rejected.

## Rollback / cleanup notes

- Data lives in the project's app DB; deletes are immediate (**data loss** — confirm on destructive ops). Reversible only via app-data backups (none by default).

## Design notes

- Isolation is enforced by **always deriving the DB from the authenticated project**, never from client input — the single rule that makes the browser safe.

## Status

⬜ todo
