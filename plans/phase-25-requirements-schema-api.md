# Phase 25 — Structured requirements schema & API

## Title & ID

- **ID:** `phase-25`
- **Title:** Structured requirements schema & API
- **Epic:** Epic 5 — Requirements & Test Generation
- **Milestone:** M3

## Goal

The fill-in-the-blanks `RequirementSpec` model + CRUD API + validation — structured input that compiles deterministically into tests.

## Context

Implements D6 (structured requirements → tests) and §6 `RequirementSpec`. Structure is what makes test generation evaluable (D13). Versioned like all artifacts (§7).

## Depends on

- **Phase IDs:** `phase-07`
- **External:** —

### Prerequisite check

- `phase-07` ✅ (artifacts/versioning); `RequirementSpec` doc from `phase-02`.

## Detailed tasks

1. **Schema (`app/orchestrator/requirements.py` + Pydantic).** `RequirementSpec = {project_id, version, features[]}` where `feature = {name, description, inputs[], expected_behaviors[], acceptance_criteria[]}`; each `acceptance_criterion = {id, text, kind: unit|e2e|either}`. Strong validation (non-empty names, unique feature names, ≥1 acceptance criterion per feature).
2. **Service.** Create/update (new **version** on change), get latest/version, list; validation errors as `UserError` with field detail.
3. **API (`app/orchestrator/router.py` additions).** `GET/POST/PUT /projects/{id}/requirements`, `GET …/requirements/versions`. Ownership-checked.
4. **Traceability keys.** Stable `criterion.id`s so generated tests (`phase-27`) and results (`phase-28`) can trace back to specific acceptance criteria (needed by the repair loop's minimal context).

## Out of scope for this phase

- The guided **UI** (`phase-26`), test generation (`phase-27`). Pure model + API + validation.

## Acceptance criteria

- Specs persist, validate (reject empty/duplicate/no-criterion), and **version** on change.
- Each acceptance criterion has a stable id and a `kind` hint.
- Retrievable latest + by version.

## Tests to write

- `backend/tests/requirements/test_schema_validation.py` — valid spec; rejects empties/dupes/missing criteria.
- `backend/tests/requirements/test_versioning.py` — edits create versions; ids stable across edits where unchanged.
- `backend/tests/requirements/test_requirements_api.py` — CRUD + ownership.

## Manual verification steps

1. `POST /requirements` a 2-feature spec → stored v1.
2. Edit a feature → v2; both retrievable; unchanged criterion ids preserved.
3. Post an invalid spec (no criteria) → 4xx with field detail.

## Rollback / cleanup notes

- Remove requirement specs for test projects. Reversible.

## Design notes

- `criterion.id` stability is deliberately load-bearing: it is the join key across requirements → tests → results → repair context.

## Status

⬜ todo
