# Phase 19 — Design stage handler & UI

## Title & ID

- **ID:** `phase-19`
- **Title:** Design stage handler & UI
- **Epic:** Epic 3 — Design Stage (pluggable)
- **Milestone:** M2

## Goal

The full Design stage: intake (screenshots / text / bring-your-own), generate, iterative chat-refine (versioned), and approve/skip — all provider-agnostic.

## Context

First real stage handler, exercising the conductor (`phase-08`), providers (`phase-16-18`), IDE/preview (`phase-14/15`), and non-linearity (D12): design can be skipped or brought in from the user's own assets, and refining marks downstream stale.

## Depends on

- **Phase IDs:** `phase-09`, `phase-14`, `phase-16`, `phase-17`
- **External:** an active design provider (Stitch or fake/Figma fallback).

### Prerequisite check

- `phase-16` ✅ (+ at least one working provider), `phase-09` ✅ (workspace), `phase-14` ✅ (to view generated HTML/CSS).

## Detailed tasks

1. **Handler (`app/orchestrator/stages/design.py`).** Implements `StageHandler`:
   - `action=proceed` with image/text intake → active provider `generate_*` → store versioned `design` artifact → emit progress + `awaiting_user`.
   - `action=refine` → provider `refine(prev_ref, instruction)` → new artifact version; mark **downstream stale** (via state machine).
   - `action=skip` → mark stage `skipped` (downstream must tolerate no design).
   - **Bring-your-own:** accept user-provided HTML/CSS/screenshots as a `design` artifact without a provider call.
   - Provider errors (quota/auth) → user-facing message + fallback per `phase-17/18`.
2. **Intake API/schema.** Extend the Intent payload for design: `{images[], text?, own_design?}`; image upload endpoint storing to blobs.
3. **Design UI (`src/features/design/`).** Upload dropzone (screenshots), text-prompt box, **design preview** (render returned HTML/CSS in a sandboxed iframe or via preview), chat-refine composer, version history (pick/compare), **Approve** / **Skip** / **Import own design** buttons; provider/health + quota indicators.
4. **Wiring.** Mount as the Design stage panel in the workspace; bind streamed events.

## Out of scope for this phase

- Turning designs into code (that's Build, `phase-23`). New providers. Requirements capture.

## Acceptance criteria

- Upload/text → preview → refine ×N → **Approve**; each refine is a **new version** (nothing overwritten).
- **Skip** works (stage `skipped`; downstream tolerates missing design).
- **Bring-your-own** design stored as an artifact without a provider call.
- Refining after completion marks downstream stages `stale`.
- Provider quota/auth failure shows a clear message + fallback, never a crash.

## Tests to write

- `backend/tests/orchestrator/stages/test_design_handler.py` — proceed/refine/skip/own-design; versioning; stale propagation; provider-error fallback (mocked provider).
- `frontend/src/features/design/DesignPanel.test.tsx` — upload, preview render, refine, approve/skip/import (mock API + WS).

## Manual verification steps

1. Upload a screenshot → preview renders → "make the header violet" refine → new version → Approve.
2. Skip design → proceed to Build; confirm downstream tolerates it.
3. Import own HTML/CSS → stored as design artifact.
4. Force provider quota error → clear message + fallback offered.

## Rollback / cleanup notes

- Remove design artifacts/blobs for test projects. Reversible.

## Design notes

- Returned HTML/CSS is previewed in a **sandboxed iframe** (untrusted markup) — never injected into the platform DOM.

## Status

⬜ todo
