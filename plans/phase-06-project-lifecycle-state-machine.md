# Phase 06 — Project lifecycle & stage state machine

## Title & ID

- **ID:** `phase-06`
- **Title:** Project lifecycle & stage state machine
- **Epic:** Epic 1 — Project & Orchestration Core
- **Milestone:** M1

## Goal

Create/list/open projects and implement the **non-linear** stage state machine (§8): enter anywhere, skip anything, jump back marks downstream **stale**, with only two hard prerequisites (deploy⇐build, validate⇐deploy).

## Context

This is the load-bearing implementation of invariant D12 and §8. Every stage handler and the conductor (`phase-08`) depend on the transition legality defined here. Getting the rules and their tests right is the whole point.

## Depends on

- **Phase IDs:** `phase-02`, `phase-03`
- **External:** —

### Prerequisite check

- `phase-02` ✅ (`Project`, `StageState` docs + repos), `phase-03` ✅ (`current_user`).
- Realtime `emit` available if wiring transition events now (soft dep on `phase-05`; degrade to no-op if absent).

## Detailed tasks

1. **Project service (`app/projects/service.py`).** `create_project` (assigns `app_db_name`, initializes 6 `StageState` rows = `empty`, sets `current_stage`), `list_for_user`, `get` (ownership-checked), `rename`, `archive/delete`.
2. **State machine (`app/projects/state_machine.py`).** Pure, well-tested transition logic:
   - **Stages:** `design, requirements, build, test, deploy, validate` (+ implicit `idle`).
   - **Statuses:** `empty | in_progress | awaiting_user | complete | skipped | stale`.
   - **Enter anywhere:** entering any stage is legal regardless of others' state (subject to hard prereqs below).
   - **Skip:** any stage → `skipped`; downstream must tolerate its missing artifacts.
   - **Jump-back / refine:** re-entering a `complete` stage opens a new round; on producing new artifacts, mark **downstream** stages `stale` (never delete their artifacts).
   - **Hard prereqs (only these):** enter/complete `deploy` requires `build` ∈ {complete}; enter/complete `validate` requires `deploy` ∈ {complete}. Everything else optional.
   - Expose `can_transition(project, stage, action) -> Result` and `apply_transition(...)` returning new states + the set of stages newly marked stale.
3. **Router (`app/projects/router.py`).** `POST /projects`, `GET /projects`, `GET /projects/{id}`, `PATCH /projects/{id}` (rename), `DELETE /projects/{id}`; `GET /projects/{id}/stages` (all StageStates); `POST /projects/{id}/stages/{stage}/transition` `{action: enter|skip|complete|refine}` → applies via state machine, persists, emits `stage.transition` events. All ownership-checked.
4. **Events.** On every transition emit `stage.transition {stage, from, to, stale: [...]}` via `phase-05` hub.
5. **Docs.** A truth-table doc/comment enumerating legal/illegal transitions (source for the tests).

## Out of scope for this phase

- Conductor routing to real handlers (`phase-08`), conversation/artifacts persistence beyond StageState refs (`phase-07`), any stage's actual work. Workspace UI (`phase-09`).

## Acceptance criteria

- Creating a project yields 6 `StageState` rows (`empty`) + an assigned `app_db_name`.
- Enter-anywhere works; skip sets `skipped`; refine on a completed stage marks strictly-downstream stages `stale` without deleting artifacts.
- `deploy` blocked unless `build` complete; `validate` blocked unless `deploy` complete; violations rejected with a `UserError`.
- Every transition emits a `stage.transition` event.
- Ownership enforced on all project routes (foreign project → 404/403).

## Tests to write

- `backend/tests/projects/test_state_machine.py` — full truth table: enter/skip/complete/refine across all stages; stale propagation; both hard prereqs (allow + reject).
- `backend/tests/projects/test_project_service.py` — create initializes stages; list scoping; ownership.
- `backend/tests/projects/test_transition_events.py` — event emitted with correct `from/to/stale` payload.

## Manual verification steps

1. `POST /projects` → note id; `GET /projects/{id}/stages` → six `empty`.
2. `transition build:complete` then `transition deploy:enter` → allowed; on a fresh project `transition deploy:enter` → rejected.
3. Complete `design`, then `refine design` → `requirements/build/...` become `stale`; artifacts still present.

## Rollback / cleanup notes

- Delete test projects (cascades StageStates). Reversible.

## Design notes

- `stale` is added to the §6 status set (plan mentions "marked stale, not deleted" in §8 prose); modeled explicitly as a `StageState.status` value.
- State machine is a **pure module** (no I/O) so it is exhaustively unit-testable; the service persists its results.

## Status

⬜ todo
