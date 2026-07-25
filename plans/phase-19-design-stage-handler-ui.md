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
  *(Revised after live Stitch runs: the frame is `sandbox="allow-scripts"`, because real exports style themselves at
  runtime via the Tailwind CDN and a script-less frame renders them unstyled — a false picture of what was generated.
  `allow-same-origin` is deliberately never granted, so the markup runs in an opaque origin and cannot reach the
  platform DOM, cookies, storage or API; forms, popups and top-navigation stay blocked. Pinned by `DesignPreview.test.tsx`.)*

## Status

✅ done — The first real stage handler + full design UI, provider-agnostic and non-linear (D12).

**Backend**
- `app/orchestrator/stages/design.py` — `DesignStageHandler` (registered as the real `design` handler
  via `stages/__init__._register_real_handlers`, replacing the stub). Maps the three UI actions onto
  the design lifecycle: `refine` = generate (text / uploaded screenshots) **or** import BYO **or**
  provider-refine the latest → new `design` version, stage → `awaiting_user`; `proceed` = approve
  (→ `complete`, no provider call); `skip` = opt out. Returns an `ArtifactSpec` so the **conductor**
  is the single persister (artifact linked to the reply + returned in the response). Refine on a
  complete stage marks downstream `stale` (via the conductor's refine action).
- `app/design/service.py` — extracted `design_artifact_fields()` (pure) so handler + `save_result`
  build identical artifacts without double-persisting.
- `app/design/{schemas,router}.py` — design intake schema (`DesignIntake`) + `POST /projects/{id}/
  design/images` (base64 JSON → blob store, capped) + `GET /projects/{id}/design/provider`
  (health + capabilities). `ProviderError.to_envelope` now includes `fallback_hint` so quota/auth
  failures surface a clear message **and** the fallback, never a crash.

**Frontend**
- `features/design/` — `DesignPanel` (text/screenshot/import intake, chat-refine, version history,
  Approve/Skip, provider health badge) + `DesignPreview` (untrusted HTML/CSS in a **fully sandboxed**
  `srcdoc` iframe, `sandbox=""`, never in the platform DOM). Lazy-mounted as the Design stage panel;
  `apiClient` now carries `fallbackHint` into toasts.

**Acceptance:** upload/text → preview → refine ×N → Approve (each refine a new version); Skip
(downstream tolerates no design); BYO stored without a provider call; refine-after-complete stales
downstream; provider quota/auth failure → clear message + fallback (no crash). All covered by
`tests/orchestrator/stages/test_design_handler.py` (10), `tests/design/test_design_router.py` (5),
and `features/design/DesignPanel.test.tsx` (6).

**Verification:** backend non-sandbox suite **221 passed** (updated 4 phase-08 tests that used
`design` as a generic stub → now `requirements`); frontend **82 passed**; ruff/black + mypy
(phase-19 surface) clean; eslint/prettier/tsc + vite build clean. Same Windows-env notes as phase-17
(isolated FastAPI 0.139.0 venv; `tests/sandbox` untouched, skipped here). Live Stitch/Figma smoke
needs real creds; covered offline via the `fake` provider.

---

## Follow-up — the project's screens, not just this version's

A real app is a *set* of screens (landing, login, signup, dashboard…) built up over many
generate/refine turns, but each design **version** records only the screen that turn produced. So
the version list could never show the whole app, and a refine always targeted whichever screen was
generated last — wrong as soon as a project has two.

- **`ScreenLister`** (`design/base.py`) — an **optional** provider capability
  (`isinstance(provider, ScreenLister)`), so `fake`/imported designs are not forced to fake one;
  `DesignCapabilities.list_screens` advertises it. Stitch implements it over its `list_screens`
  tool, returning `DesignScreenRef{ref, title, preview_image}` where `ref` is the same handle
  `fetch_code`/`refine` already take.
- **API** — `GET /projects/{id}/design/screens` (the whole project; empty, never an error, when the
  provider can't enumerate) and `GET /projects/{id}/design/screen?ref=…` (one screen's markup,
  fetched on demand rather than bloating every artifact).
- **UI** — a **screen dropdown** above the preview whenever the project has more than one, opening
  on the screen the current version produced. Picking another previews it immediately.
- **Refine follows the eye** — `DesignIntake.design_ref` carries the shown screen, so "make this
  darker" changes what you are looking at. Reviewing the newest design and then correcting an older
  screen is now a two-click flow instead of impossible.

Two bugs found alongside: a refine sent from the **conversation** composer invalidated only
`messages`+`stages`, so a new design version stayed invisible (`ChatPanel` now invalidates every
query scoped to the project); and the panel's "follow the newest version" comment did not match its
code, which only moved the selection when it became *invalid*. Both are pinned by tests, as is the
race where a slow artifact load would stomp a screen the user had already picked.
