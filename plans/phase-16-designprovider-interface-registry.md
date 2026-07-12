# Phase 16 — DesignProvider interface & registry

## Title & ID

- **ID:** `phase-16`
- **Title:** DesignProvider interface & registry
- **Epic:** Epic 3 — Design Stage (pluggable)
- **Milestone:** M2

## Goal

A provider-agnostic design abstraction (`DesignProvider`) plus a registry/selection mechanism and a fake provider, so the design stage works end-to-end before any real MCP integration.

## Context

Implements D9 (pluggable design; Stitch default, Figma alt) and hedges D10 (Stitch quota/auth risk). Defining the interface first keeps stage handlers provider-independent (D9 acceptance: switching providers requires no handler change).

## Depends on

- **Phase IDs:** `phase-08`
- **External:** —

### Prerequisite check

- `phase-08` ✅ (conductor + stage handler interface + artifacts).

## Detailed tasks

1. **Interface (`app/design/base.py`).** `class DesignProvider(Protocol)`:
   - `generate_from_image(images, prompt?) -> DesignResult`
   - `generate_from_text(prompt) -> DesignResult`
   - `fetch_code(design_ref) -> {html, css, assets}`
   - `refine(design_ref, instruction) -> DesignResult`
   - `capabilities() -> {...}` and `health() -> ok|degraded|down`.
   - `DesignResult = {provider, external_ref, html, css, preview_image?, meta}`.
2. **Registry (`app/design/registry.py`).** Register providers by key (`stitch`, `figma`, `fake`); select active provider from config resolver (`admin > env > default` — so the admin dashboard can switch providers, `phase-51/52`); expose `get_active(project?)` with per-project override support.
3. **Artifact mapping.** Map `DesignResult` → `design` `Artifact` versions (`phase-07`), storing HTML/CSS via the blob layer and a preview image reference; `meta` records provider + external ref for later `fetch_code`/`refine`.
4. **Fake provider (`app/design/fake.py`).** Deterministic provider returning canned HTML/CSS (and echoing refine instructions) for tests and offline dev/demo fallback.
5. **Errors.** Provider failures raised as `ProviderError` with a `fallback_hint` (used by graceful degradation in `phase-48`).

## Out of scope for this phase

- Real Stitch (`phase-17`) / Figma (`phase-18`) adapters; the design **stage handler + UI** (`phase-19`). Only the contract, registry, mapping, and fake provider here.

## Acceptance criteria

- The design stage handler (stubbed via the fake provider) runs end-to-end: text/image → `DesignResult` → versioned `design` artifact.
- Switching the active provider is a **config change only** — no handler/registry consumer code changes.
- Provider errors surface as `ProviderError` with a fallback hint.

## Tests to write

- `backend/tests/design/test_registry.py` — selection via config; per-project override; unknown key error.
- `backend/tests/design/test_fake_provider.py` — all interface methods; artifact mapping + versioning.
- `backend/tests/design/test_result_to_artifact.py` — HTML/CSS to blob + meta correctness.

## Manual verification steps

1. Set active provider = `fake`; run a design intent → `design` artifact created with canned HTML/CSS.
2. Call `refine` → new artifact version referencing the prior.

## Rollback / cleanup notes

- Remove design artifacts from test projects. Reversible.

## Design notes

- **Per-project provider override** (not just global) is included so a project can pin Figma if Stitch quota is exhausted — supports D10 fail-soft. Resolution order: project override → admin/global config → env → default.

## Status

⬜ todo
