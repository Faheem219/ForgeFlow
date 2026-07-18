# Phase 26 — Requirements UI (guided form)

## Title & ID

- **ID:** `phase-26`
- **Title:** Requirements UI (guided form)
- **Epic:** Epic 5 — Requirements & Test Generation
- **Milestone:** M3

## Goal

An easy, guided per-feature capture form (add feature → fill blanks → review) bound to the requirements stage, usable by a semi-technical user.

## Context

Delivers the "fill-in-the-blanks guide" UX of D6. Structured, low-friction capture is what makes the downstream test generation reliable and evaluable.

## Depends on

- **Phase IDs:** `phase-09`, `phase-25`
- **External:** —

### Prerequisite check

- `phase-25` ✅ (requirements API), `phase-09` ✅ (workspace shell).

## Detailed tasks

1. **Requirements feature (`src/features/requirements/`).** A form builder: list of features; **Add feature** → fields for name, description, repeatable **inputs**, **expected behaviors**, and **acceptance criteria** (with `kind` selector). Inline help/examples per field; validation mirroring the API.
2. **Review summary.** A read view of the whole spec (features → criteria) with edit affordances; "save" posts a new version via `phase-25`.
3. **Stage binding.** Mounts as the Requirements stage panel; refine/skip/proceed affordances; on save, if a build exists, mark test/downstream `stale`.
4. **Import assist (optional, Haiku).** A "suggest criteria from description" helper that proposes acceptance criteria the user edits/accepts (routed to `MODEL_ROUTING`; user stays in control). Clearly optional and cheap.

## Out of scope for this phase

- Test generation (`phase-27`), running tests (`phase-28`). Only capture + review UI.

## Acceptance criteria

- A user builds a **multi-feature** spec entirely via forms; it saves and is editable (new versions).
- Validation errors are shown inline; a valid spec round-trips to the API.
- Optional AI-suggest proposes editable criteria (Haiku) without overwriting user input.

## Tests to write

- `frontend/src/features/requirements/RequirementsForm.test.tsx` — add feature, fill blanks, validation, save (mock API).
- `frontend/src/features/requirements/ReviewSummary.test.tsx` — renders spec; edit → save new version.

## Manual verification steps

1. Add two features with inputs/behaviors/criteria → save → reload → intact.
2. Trigger a validation error (empty criterion) → inline message.
3. (Optional) Use AI-suggest → editable proposals appear.

## Rollback / cleanup notes

- UI-only. Reversible.

## Design notes

- AI-suggest is intentionally **assistive and optional** (Haiku, cheap) to keep the structured-input contract (D6) intact — the human owns the spec.

## Status

✅ done

**Implementation notes**

- Backend: added an optional AI-assist endpoint `POST /projects/{id}/requirements/suggest` — [requirements_suggest.py](../backend/app/orchestrator/requirements_suggest.py) routes to `MODEL_ROUTING` (Haiku, `TaskKind.summarize`), best-effort JSON parse, cost-tracked on a `Run`. Ownership-checked; degrades soft (502, never 500) when no model/creds. Schemas (`SuggestRequest`/`SuggestResponse`) in [requirements.py](../backend/app/orchestrator/requirements.py); route in [router.py](../backend/app/orchestrator/router.py).
- Frontend feature `src/features/requirements/`: `RequirementsForm` (guided multi-feature capture with inputs/behaviors/criteria + `kind` selector, inline validation mirroring the API, per-feature "Suggest (AI)" that **appends** editable proposals), `ReviewSummary` (read view + Edit), `RequirementsPanel` (loads latest spec, review⇄edit toggle, status badge + Proceed/Skip via the conductor, save → on an existing build a `refine` intent stales downstream). Mounted lazily in `StagePanel`.
- Staleness: a save only fires the downstream-staling `refine` when a build exists **and** requirements is in a reopenable status (`complete`/`skipped`/`stale`) — the conductor rejects a refine otherwise, so we never emit an illegal intent.
- Tests: `RequirementsForm.test.tsx` (5) + `ReviewSummary.test.tsx` (2) green; backend `test_requirements_suggest.py` (8). Full suites: frontend 94 passed, backend 293 passed (1 docker deselected).
