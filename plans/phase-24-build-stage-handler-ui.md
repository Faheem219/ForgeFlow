# Phase 24 — Build stage handler & UI

## Title & ID

- **ID:** `phase-24`
- **Title:** Build stage handler & UI
- **Epic:** Epic 4 — Build Stage (agentic codegen)
- **Milestone:** M2

## Goal

Wrap the codegen agent in a Build stage handler with a conversational change-request loop, and a Build panel showing live generation, preview, "request changes", and proceed/skip.

## Context

Makes the codegen engine (`phase-23`) a first-class, steerable stage honoring D12 (skip to deploy, refine conversationally). Completes milestone M2 (screenshots/text → running app with live code + change requests).

## Depends on

- **Phase IDs:** `phase-09`, `phase-23`
- **External:** —

### Prerequisite check

- `phase-23` ✅ (codegen), `phase-09` ✅ (workspace shell), IDE (`phase-14`) + preview (`phase-15`) available.

## Detailed tasks

1. **Handler (`app/orchestrator/stages/build.py`).** Implements `StageHandler`:
   - `action=proceed` → run codegen (`phase-23`) → build report artifact → `awaiting_user`.
   - `action=refine` (change request) → run codegen incrementally with the user's instruction + prior build report → new version; downstream (test/deploy/validate) marked `stale`.
   - `action=skip` → mark `skipped` (allows skip-to-deploy if a build already exists; otherwise the deploy hard-prereq blocks).
   - Surface budget/provider errors as clear messages.
2. **Build UI (`src/features/build/`).** Live-generation view (embeds the IDE stream + terminal), preview pane, **build report** summary (features/files/boot status), a **"Request changes"** chat, and **Proceed** / **Skip to deploy** actions. Progress + cost indicators from events.
3. **Wiring.** Mount as the Build stage panel; bind `agent.token`, `fs.write`, `terminal.output`, `preview.status`, and cost events.

## Out of scope for this phase

- Requirements capture (`Epic 5`), test/repair (`Epic 5/6`), deployment (`Epic 7`). The codegen internals (`phase-23`).

## Acceptance criteria

- Generate → preview → **request a change** → regenerate incrementally (new version; downstream marked `stale`).
- **Skip-testing / skip-to-deploy** path available (subject to the deploy hard-prereq).
- Live generation, preview, and build report all render; cost/progress visible.

## Tests to write

- `backend/tests/orchestrator/stages/test_build_handler.py` — proceed/refine/skip; downstream stale on refine; error surfacing (mock codegen).
- `frontend/src/features/build/BuildPanel.test.tsx` — live view, request-changes, proceed/skip (mock API + WS).

## Manual verification steps

1. Proceed → app generates + previews; read the build report.
2. "Add a delete button" → incremental regenerate; test/deploy badges flip to `stale`.
3. Skip to deploy (with an existing build) → allowed; on a fresh project → blocked with a clear reason.

## Rollback / cleanup notes

- Revert workspace to a prior build commit; artifacts versioned. Reversible.

## Design notes

- The change-request loop reuses the same incremental codegen path (`phase-23`), so "refine" and "initial build" share one code path — fewer surprises, cheaper to maintain.

## Status

✅ done — **completes M2**.

### Completion notes

- **Handler** [`app/orchestrator/stages/build.py`](../backend/app/orchestrator/stages/build.py)
  (registered in `stages/__init__.py`): `proceed`/`refine` run the codegen agent (phase-23) on a
  distinct `Run` (kind `codegen:build`); `skip` marks the stage skipped. A **healthy boot →
  `complete`** (so deploy's hard-prereq is satisfiable), a broken boot → `awaiting_user`. Budget/
  provider errors propagate to the conductor → the API error envelope + `budget.halt` event.
- **Change requests:** `CodegenAgent.run` gained an additive `instruction` kwarg (threaded into the
  plan + implement prompts) so a `refine` regenerates incrementally; the conductor's `refine` action
  over a complete build marks downstream (test/deploy/validate) `stale` automatically. Initial build
  and change request share ONE code path.
- **UI** [`src/features/build/BuildPanel.tsx`](../frontend/src/features/build/BuildPanel.tsx):
  live-generation IDE stream + toggleable preview, the structured **build report** (boot status,
  features, files changed, follow-ups, notes), a **Request changes** chat, and **Build/Rebuild** +
  **Skip** actions; live progress from `progress` events. Replaces the old inline build case in
  `StagePanel`.
- **Action-mapping decision (resolves a roadmap ambiguity — Design note):** the plan listed both
  `proceed` and `refine` as "run codegen" yet also required `refine → downstream stale`, which needs
  the build to be `complete` when a change is requested. Resolution: `proceed` = build →
  `complete`-on-healthy (initial/rebuild), `refine` = change request (→ stale downstream), `skip` =
  skip. This satisfies every acceptance criterion (generate → change → stale; skip-to-deploy subject
  to the hard-prereq) and keeps deploy reachable.
- **Ripple:** four existing conductor tests used `build` as a convenient stub stage (as they'd used
  `design` before phase-19); switched them to the still-stubbed `test` stage / a direct state
  transition. `test_conductor_routing` no longer expects a `build` stub.
- **Verification:** frontend **87/87 vitest + ESLint + tsc clean** (BuildPanel: report render,
  proceed, refine-with-message, skip, preview toggle). Backend ruff/black/mypy clean; a harness
  confirmed the handler registration/import chain, the new `instruction` kwarg, and `_summarize`
  output; the DB-backed handler tests (`test_build_handler.py`: proceed/refine/skip/boot-gating/
  stale/error) run on Linux/CI.
- **Out of scope (as planned):** requirements capture (Epic 5), test/repair (Epic 5/6), deploy
  (Epic 7), and the codegen internals (phase-23).
