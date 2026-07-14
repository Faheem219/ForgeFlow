# Phase 09 — Workspace UI shell

## Title & ID

- **ID:** `phase-09`
- **Title:** Workspace UI shell
- **Epic:** Epic 1 — Project & Orchestration Core
- **Milestone:** M1

## Goal

The project workspace: a clickable **stage navigator** + chat panel + artifact/preview panel scaffolds, wired to the conductor and realtime stream, honoring non-linearity (any stage clickable; each panel has refine/skip/proceed).

## Context

The UI contract from §8. This shell is where every later stage panel (design, IDE, requirements, testing, deploy, data, eval) mounts. It must make non-linearity visible and drive the stub conductor end-to-end.

## Depends on

- **Phase IDs:** `phase-04`, `phase-05`, `phase-08`
- **External:** —

### Prerequisite check

- `phase-04` ✅ (shell/UI kit), `phase-05` ✅ (WS client), `phase-08` ✅ (`/intent` + stub handlers).

## Detailed tasks

1. **Dashboard (`src/features/dashboard/`).** Project list (TanStack Query on `/projects`), create-project modal, open → `/projects/:id`.
2. **Workspace layout (`src/features/workspace/`).** Three-region layout: left **StageNavigator**, center **StagePanel outlet**, right **Chat/Activity** panel. Bind `realtimeStore` (subscribe to the project channel on mount; resume via `last_seq`).
3. **StageNavigator component.** Renders all six stages with status badges (`empty/in_progress/awaiting_user/complete/skipped/stale`); **any stage clickable** (enter-anywhere); shows hard-prereq lock hints on `deploy`/`validate`; per-stage overflow menu → **refine / skip / proceed**.
4. **Chat panel.** Message list (from `/projects/:id/messages?stage=`), composer that posts `/projects/:id/intent`; renders streamed `agent.token`/`progress` events live; shows tool/system messages distinctly.
5. **StagePanel scaffolds.** One placeholder panel per stage exposing the refine/skip/proceed affordances and an artifacts area; real content fills in later epics. A shared `useStage(projectId, stage)` hook.
6. **State stores (`src/lib/stores/`).** `workspaceStore` (active stage, panel state), integrate `realtimeStore`.

## Out of scope for this phase

- Real stage panels' internals (design upload, Monaco IDE, requirements form, test/repair view, deploy topology, data browser) — their own phases. Only scaffolds + navigation + chat here.

## Acceptance criteria

- User can open a project, click **any** stage (enter-anywhere), and send an intent that reaches the stub conductor; streamed responses render live.
- Skip and jump-back are reflected in the navigator (statuses update from `stage.transition` events, incl. `stale`).
- Hard-prereq stages show a lock hint until satisfied.
- Reconnecting mid-stream resumes without losing events (uses `phase-05` resume).

## Tests to write

- `frontend/src/features/workspace/StageNavigator.test.tsx` — renders statuses; click emits stage change; lock hints for deploy/validate.
- `frontend/src/features/workspace/ChatPanel.test.tsx` — posts intent; renders streamed tokens (mock WS).
- `frontend/src/features/dashboard/Dashboard.test.tsx` — list + create flow (mock API).

## Manual verification steps

1. Create a project → open workspace → six stages visible with statuses.
2. Click `build` (skipping design) → send "go" → stub response streams into chat.
3. Complete `build` (stub) → `deploy` unlocks; refine `design` → downstream badges flip to `stale`.
4. Reload mid-stream → activity resumes.

## Rollback / cleanup notes

- UI-only; no persistent server state beyond what the conductor writes. Reversible.

## Design notes

- Panels are lazy-loaded per stage so later epics add heavy deps (Monaco, React Flow) without bloating the initial workspace bundle.

## Status

✅ done
