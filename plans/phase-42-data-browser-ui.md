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

⬜ todo
