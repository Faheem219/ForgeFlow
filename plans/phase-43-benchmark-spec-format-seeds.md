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

✅ done — the benchmark format, a validating loader, and **9 graded seeds**. Opens Epic 10, the
evaluation harness that turns the repair loop into publishable evidence (D13).

- `app/eval/specs_loader.py` — `EvalSpec {id, title, difficulty, inputs{prompt?, screenshots[]},
  requirements, expected_features[], tags[], notes}` plus `load_spec`, `load_all`, `spec_index`, and
  a `--list` / `--quiet` CLI.
- **One schema, not two.** A spec's `requirements` block *is* `RequirementSpecInput` — the very type
  the requirements API accepts — and the loader validates it with the product's own validator, newly
  exposed as `validate_features()` (a thin public wrapper over phase-25's `_resolve`). A seed the
  loader accepts is therefore one the product accepts; the two cannot drift. Criterion **ids are not
  authored**: they are minted by the same code that mints them in the product, because ids are only
  stable within a project's own spec history.
- **Semantic validation beyond the schema:** `id` must equal the filename stem (the file is the
  record), at least one input is required, every `expected_features` entry must name a real feature,
  and referenced screenshots must exist on disk.
- **Fails loud, never quiet.** One malformed spec fails the *whole* load, and a missing or empty
  specs directory is an error rather than an empty corpus — a benchmark that silently shrinks would
  flatter its own results. (The empty-directory case was a real bug found during manual verification:
  the CLI exited 0 on a nonexistent path. Fixed and covered.)
- `eval/specs/` — 9 seeds, **3 simple / 4 moderate / 2 complex**, 26 features and 86 acceptance
  criteria: `todo-list`, `notes-crud`, `url-shortener`, `expense-tracker`, `bookmark-manager`,
  `recipe-box`, `kanban-board` (also screenshot-driven, with a generated wireframe in `assets/`),
  `issue-tracker`, `event-scheduler`. Each seed carries a `notes` field saying what it is meant to
  catch, and each has at least one criterion a *plausible-but-wrong* implementation fails —
  conjunctive tag filtering rather than disjunctive, time ranges that touch but do not overlap,
  scaling that must not persist. A benchmark whose criteria any build passes measures nothing.
- `eval/specs/schema.md` — the format, the difficulty rubric, and how to add a spec.
- `pyyaml` added as an explicit dependency (it was only present transitively); the loader also
  accepts `.yml` and `.json`.

**Acceptance:** all seeds load and validate; the loader rejects malformed specs with the file *and*
the reason; specs reuse the `RequirementSpec` schema as the single source of truth; 5–10 graded seeds
with inputs, requirements and expected features. Covered by `tests/eval/test_specs_loader.py` (25)
and `tests/eval/test_spec_schema.py` (7) — including that the corpus spans all three difficulties and
that harder seeds really are larger, so the grading tracks substance rather than a label.

**Verification:** ruff/black/mypy clean; full backend suite **720 passed** (+32) with the 8 known
pre-existing env-only failures unchanged. Both manual steps run: `--list` prints the 9-spec index
with no errors, and a corrupted spec reports
`todo-list.yaml: requirements are invalid: Feature 'No criteria' needs at least one acceptance
criterion` — note that message comes from the *product's* validator, which is the schema reuse
demonstrating itself. Live-verified **7/7** against real MongoDB: every seed's requirements block is
accepted **verbatim by the real `PUT /requirements` endpoint**, the hardest seed yields 15 unique
criterion join keys, and re-submitting preserves every id (the phase-25 invariant phase-44 needs to
compare runs).

**Deferred (as scoped):** the headless runner is phase-44 and the dashboard phase-45 — this phase is
format, seeds and loader only.
