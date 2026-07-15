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

✅ done — **Opens Epic 3 / M2.** `app/design/base.py` defines the provider-agnostic contract:
`DesignProvider` Protocol (`generate_from_image`/`generate_from_text`/`fetch_code`/`refine`/
`capabilities`/`health`), `DesignResult{provider, external_ref, html, css, preview_image, meta}`,
`DesignCode`, `DesignImage`, `ProviderHealth`, plus a `provider_error()` helper that **always**
attaches a `fallback_hint` (design is the most failure-prone dependency — D10 — and phase-48 turns
those hints into graceful degradation). `app/design/fake.py`: a deterministic fake (same input →
same `external_ref` + markup; image refs key on **pixels, not filename**; `refine` echoes the
instruction and chains; unknown refs raise `ProviderError`) — it is both the test double and the
offline dev/demo fallback. `app/design/registry.py`: register/`get_active` resolving **project
override → admin/env/default**; unknown keys raise an actionable `UserError` listing what *is*
available. New `Project.design_provider` field carries the per-project override (D10: pin Figma when
Stitch's quota is out, without affecting other projects); surfaced on `ProjectPublic`/`ProjectSummary`.
`app/design/service.py` maps `DesignResult` → versioned `design` artifacts and exposes the
provider-agnostic flows (`generate_from_text`/`generate_from_images`/`refine`).

**Payload decision:** phase-07 artifacts carry a single text payload, so HTML+CSS ride in one JSON
envelope — a version stays atomic (markup and styles can't drift apart) and inherits phase-07's
inline-vs-blob strategy for free. Structured fields (provider, external ref, preview image, source,
`refined_from_version`) live in `meta`, with provider-specific meta **nested** under `provider_meta`
so it can never clobber ours (covered by a test).

Verified: ruff/black/mypy + eslint/prettier/tsc clean; **backend 268 passed** (30 new — registry
selection/override/unknown/replace, the full fake contract incl. determinism + pixel-keyed refs +
ProviderError hints, and artifact mapping: payload round-trip, meta, clobber-resistance, inline↔blob
offload, append-only versioning, and text/image→artifact + refine→new version end-to-end) **+
frontend 76 passed**. **Live against real MongoDB — 18/18**: text intent → v1 with canned HTML/CSS,
image → v2, refine → v3 referencing v2 with the instruction echoed, history append-only with v1
intact, `fetch_code` resolving the stored ref, **the same service call site persisting a `figma`
artifact after only a config change (D9)**, per-project override beating the global default while
other projects are unaffected (D10), and both error paths (ProviderError with hint / unknown key).

**Notes:** the default config remains `design_provider=stitch`, which is *not registered until
phase-17* — `get_active()` therefore raises a clear "Unknown design provider" until then, by design;
set `fake` for offline dev (as the plan's manual steps direct). No silent auto-fallback was added:
fallback policy belongs to phase-19/48, and masking a misconfiguration here would hide it.
**Deferred (scoped):** real Stitch (phase-17) / Figma (phase-18) adapters and the design stage
handler + UI (phase-19); this phase is contract, registry, mapping, and fake only — the pipeline is
proven end-to-end through the fake, with no conductor wiring.
