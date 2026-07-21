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

✅ done — scoped CRUD over a single project's database, with isolation enforced **structurally**
rather than by checking. Opens Epic 9 / M5.

- `app/data_browser/service.py` — `DataBrowserService`: `list_collections`, `find` (filter/sort/
  paged), `get`, `count`, `insert`, `update`, `delete`, `db_info`. The security model is one rule:
  **the database is always derived from the authenticated project, never from client input.** There
  is no parameter anywhere in the service or the router that takes a database name, so no request
  can express "use that database" — two tests assert that property by *introspecting the signatures*,
  so it cannot regress silently.
- Two guards back that up rather than trusting it: the resolved DB is checked against the project
  (`DbProvisioner.assert_owns_db`, the phase-36 hook), and the **control-plane DB is refused
  outright** whatever the config says. That second one is not theoretical — in local dev the app data
  and `forgeflow_meta` live on the same server.
- `app/data_browser/guards.py` — pure query guardrails. Filters are validated against an **operator
  allowlist**, so JS-evaluating operators (`$where`, `$function`, `$accumulator`, `$expr`) are refused
  *and so is any operator nobody has heard of yet*: the check fails **closed**, which is what keeps it
  correct as MongoDB grows. Nesting is checked recursively (a buried `$where` is the interesting
  attack). Also: `system.*` collections refused, filter depth/size capped, regex length capped, sort
  restricted to plain field→direction pairs, page size capped at 200 and a 5s server-side time budget
  so one project's browsing cannot become a DoS on a shared cluster.
- `app/data_browser/router.py` — `GET /data/info`, `GET /data/collections`, and
  `GET|POST|PUT|DELETE /data/collections/{c}/docs[/{id}]`. Ownership is resolved through `get_owned`
  on every call, so another user's project is a `404` (existence never leaked). `/data/info` reports
  mode/db-name/managed and deliberately has **no URI field** — the URI is a secret (§7).
- Details that make it usable: `ObjectId`/datetime values are serialized for the client; documents
  with string or numeric `_id`s work (generated apps choose their own); `_id` is stripped on update
  so the UI can round-trip a whole document. **BYO** URIs work unchanged — the user's URI grants
  exactly what it grants and ForgeFlow adds no privileges.

**Acceptance:** CRUD works against the owning project's DB only; cross-project and `forgeflow_meta`
access are impossible (guarded *and* tested); dangerous operators are rejected and page size/time are
capped. Covered by `test_scoped_crud.py` (13), `test_isolation.py` (8) and `test_query_guardrails.py`
(33) — **54 tests, run against real MongoDB**, because a claim about *where a query lands* cannot be
demonstrated with a fake.

**Verification:** ruff/black/mypy clean; full backend suite **688 passed** (+54) with the 8 known
pre-existing env-only failures unchanged. Live-verified **10/10** against real MongoDB over real
HTTP, with writes made through the API and then confirmed through an **independent** Mongo
connection: a document written by project A is present in A's database and absent from B's *and* from
the control plane; cross-project reads 404; `$where` (including nested) 400s; `limit=100000` clamps
to 200; `/data/info` carries no URI. The sharpest check: requesting the `users` collection through
the API returns **0 documents** while `forgeflow_meta.users` genuinely holds 2 — the name resolves
inside the project's own database, exactly as intended.

**Deferred (as scoped):** the browser UI is phase-42; aggregation pipelines and advanced querying are
out of scope (basic filter/sort only). Deletes are immediate and irreversible — the endpoint is
documented as such, and the confirmation UX belongs with the UI in phase-42.
