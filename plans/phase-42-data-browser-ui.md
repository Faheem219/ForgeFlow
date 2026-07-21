# Phase 42 — Data browser UI

## Title & ID

- **ID:** `phase-42`
- **Title:** Data browser UI
- **Epic:** Epic 9 — Project Data Browser
- **Milestone:** M5

## Goal

In-app data management: collections list, document table, view/edit/create/delete, and simple query/filter — all scoped to the project.

## Context

The user-facing half of D8's data browser: a technical-ish user manages their generated app's data without leaving ForgeFlow or touching Atlas directly.

## Depends on

- **Phase IDs:** `phase-09`, `phase-41`
- **External:** —

### Prerequisite check

- `phase-41` ✅ (scoped data API), `phase-09` ✅ (workspace shell).

## Detailed tasks

1. **Data feature (`src/features/data/`).** Collections sidebar (from `/data/collections`); a **document table** (paginated, sortable) for the selected collection; a **filter bar** (simple field/op/value → API filter); row actions view/edit/delete; **create** modal. JSON editor for document view/edit (validated).
2. **Safety UX.** Confirm destructive actions; show scope ("Project X database") clearly; surface API guardrail errors.
3. **Wiring.** Mount as the Data stage/panel in the workspace; TanStack Query for fetching + optimistic-ish updates with invalidation.

## Out of scope for this phase

- Advanced aggregation UI, schema inference beyond field display, bulk import/export (post-v1).

## Acceptance criteria

- User browses collections, pages/sorts documents, filters, and performs **create/view/edit/delete** end-to-end.
- Destructive actions confirm; scope is always visible.
- Only the project's data is reachable (backed by `phase-41` guards).

## Tests to write

- `frontend/src/features/data/CollectionsList.test.tsx` — lists collections; select loads docs (mock).
- `frontend/src/features/data/DocTable.test.tsx` — pagination/sort/filter (mock).
- `frontend/src/features/data/DocEditor.test.tsx` — create/edit/delete flows + confirm (mock).

## Manual verification steps

1. Open Data panel → pick a collection → table renders with paging/sort.
2. Filter by a field → results narrow.
3. Create → edit → delete a document → reflected on refresh.

## Rollback / cleanup notes

- UI-only; deletes hit real app data (confirm-gated). Reversible only via app-data backups.

## Design notes

- Editing documents as validated JSON keeps the browser schema-agnostic (generated apps vary) while still guarding malformed writes.

## Status

✅ done — in-app data management over the project's own database: collections, a paged/sortable
document table, a filter bar, and create/view/edit/delete. Completes **Epic 9**.

- `features/data/DataBrowser.tsx` — composes the feature and is mounted as a workspace **view**
  (a "Data" toggle beside Terminal), not a stage: data is not part of the pipeline and should be
  reachable whatever stage you are on. Lazy-loaded into its own chunk.
- `CollectionsList.tsx` — collections with document counts; auto-selects the first so the panel is
  never an empty stare. Counts are MongoDB's fast estimates, which is the right trade for a sidebar.
- `DocTable.tsx` + `table.ts` — columns are **derived from the documents themselves** (generated apps
  have no schema ForgeFlow knows about), unioned across the page with `_id` pinned first; objects
  collapse to compact JSON. Sorting cycles ascending → descending → unsorted; paging is bounded by
  the API's own page cap.
- `FilterBar.tsx` + `filters.ts` — a small field/op/value vocabulary rather than a raw query box.
  **Every operator it can emit is on the phase-41 allowlist**, so ordinary filtering can never trip a
  guardrail; `contains` escapes regex metacharacters so user text matches literally; values are
  coerced through JSON so `42`/`true`/`null` behave as people expect.
- `DocEditor.tsx` — documents are edited as **validated JSON**, which keeps the browser
  schema-agnostic while still refusing malformed writes before they reach the API. The whole
  document round-trips (`_id` included; the server ignores it on update). API guardrail rejections
  render **next to the document** rather than in a toast that vanishes while the user is still
  looking at it, and the editor stays open so they can fix and retry.
- **Safety UX:** deletes go through an explicit `DeleteConfirm` naming the document, the collection
  and that it cannot be undone — this is live application data with no backups. The scope banner is
  always visible and distinguishes a ForgeFlow-managed `project database` from `your database` (BYO),
  because destructive actions should never be taken while unsure which database you are in.

**Acceptance:** browse collections, page/sort/filter documents, and create/view/edit/delete
end-to-end; destructive actions confirm; scope is always visible; only the project's data is
reachable (structurally, by phase-41 — the API takes no database name at all). Covered by
`CollectionsList.test.tsx` (8), `DocTable.test.tsx` (15) and `DocEditor.test.tsx` (11).

**Verification:** lint/typecheck/format clean and **184 frontend tests passing** (+34); `pnpm build`
succeeds with the browser code-split into its own 12.7 kB chunk. Live-verified **9/9** against the
real phase-41 API and real MongoDB — a *contract* check, since the frontend types and URL building
were written from the backend's models and nothing had yet proven they agree at runtime: every DTO's
field set matches exactly, the UI's query-string construction pages and sorts correctly, **all six
filter-bar operators are accepted by the real allowlist**, `contains` escaping means `a.c` matches
nothing rather than `abc`, the error envelope is the shape `ApiError` parses, a whole-document PUT
round-trips `_id`, and a delete is confirmed in the database.

**Deferred (as scoped):** aggregation UI, schema inference beyond field display, and bulk
import/export are post-v1. Sorting is single-column (the API accepts up to three).
