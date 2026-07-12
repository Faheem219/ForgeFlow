# Phase 43 — Benchmark spec format & seeds

## Title & ID

- **ID:** `phase-43`
- **Title:** Benchmark spec format & seeds
- **Epic:** Epic 10 — Evaluation Harness
- **Milestone:** M5

## Goal

Define the evaluation spec format and seed 5–10 representative benchmark specs under `eval/specs/`.

## Context

The eval harness is a first-class deliverable (D13) that turns ForgeFlow into publishable/pitchable evidence — measuring first-pass vs post-repair pass rate, repair iterations, and screenshot→URL time. Reproducible specs are the foundation.

## Depends on

- **Phase IDs:** `phase-25`
- **External:** —

### Prerequisite check

- `phase-25` ✅ (`RequirementSpec` schema — specs reuse it).

## Detailed tasks

1. **Spec format (`eval/specs/schema.md` + a Pydantic loader `app/eval/specs_loader.py`).** `EvalSpec = {id, title, inputs:{screenshots[]?, prompt?}, requirements: RequirementSpec, expected_features[], notes?}`. Reuse the `phase-25` requirements schema so eval and product share one contract.
2. **Seeds (`eval/specs/*.yaml|json`).** 5–10 representative web-app specs of graded difficulty (e.g., todo, notes CRUD, URL shortener, kanban, expense tracker) — each with input prompt/screenshot refs, structured requirements, and expected features.
3. **Loader + validation.** Load + validate all specs; fail on malformed specs; expose an index.
4. **Fixtures.** Any referenced screenshots stored under `eval/specs/assets/`.

## Out of scope for this phase

- The runner (`phase-44`), the dashboard (`phase-45`). Format + seeds + loader only.

## Acceptance criteria

- All seed specs **load and validate**; the loader rejects malformed specs.
- Specs reuse the `RequirementSpec` schema (single source of truth).
- 5–10 graded seeds present with inputs + requirements + expected features.

## Tests to write

- `backend/tests/eval/test_specs_loader.py` — all seeds load + validate; a malformed spec is rejected.
- `backend/tests/eval/test_spec_schema.py` — `EvalSpec` embeds a valid `RequirementSpec`.

## Manual verification steps

1. `uv run python -m app.eval.specs_loader --list` → lists all seeds, no validation errors.
2. Corrupt one spec → loader reports the specific failure.

## Rollback / cleanup notes

- Specs are in-repo files; removable. Reversible.

## Design notes

- Graded difficulty (simple CRUD → multi-feature) lets the eval show *where* the repair loop earns its keep — the harder specs are where first-pass fails and repair matters (D1/D13).

## Status

⬜ todo
