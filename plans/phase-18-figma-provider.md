# Phase 18 — Figma MCP provider (alternative)

## Title & ID

- **ID:** `phase-18`
- **Title:** Figma MCP provider (alternative)
- **Epic:** Epic 3 — Design Stage (pluggable)
- **Milestone:** M2

## Goal

A second design provider (Figma MCP) implementing the same `DesignProvider` interface, proving pluggability and providing a fallback when Stitch quota/auth fails.

## Context

D9's second half: pluggability must be real, not theoretical. Figma is the alternative and the natural fallback target for D10's quota risk. Acceptance for the epic is that switching providers requires **no** stage-handler change.

## Depends on

- **Phase IDs:** `phase-16`
- **External:** Figma access token (`FIGMA_*`).

### Prerequisite check

- `phase-16` ✅; `FIGMA_*` token present (else `health()=down`).

## Detailed tasks

1. **Adapter (`app/design/figma.py`).** Implement `DesignProvider` against the Figma MCP: map available Figma capabilities to `generate_from_image`/`generate_from_text`/`fetch_code`/`refine` (where a capability is unavailable, degrade clearly rather than fake success); return `DesignResult`.
2. **Auth (`app/design/figma_auth.py`).** Token-based auth from vault/config; never logged.
3. **Registry wiring.** Register under key `figma`; selectable via config and as the **fallback** target the Stitch adapter names in its `fallback_hint`.
4. **Capability reporting.** `capabilities()` accurately reflects what Figma supports so the UI/handler can adapt (e.g., hide image→UI if unsupported).
5. **Config.** `FIGMA_*` via resolver (`admin > env > default`).

## Out of scope for this phase

- Design UI (`phase-19`); the Stitch adapter (`phase-17`). Only the Figma adapter + registration.

## Acceptance criteria

- Switching the active provider from `stitch` to `figma` (config-only) runs the design stage with **no** handler changes.
- Figma produces a `design` artifact via the same mapping.
- `capabilities()` truthfully reports supported operations; unsupported ops degrade with a clear message (no fake results).

## Tests to write

- `backend/tests/design/test_figma_mock.py` — supported ops → artifact against a mocked MCP.
- `backend/tests/design/test_provider_switch.py` — same handler + intent works under both `fake`/`figma` (and `stitch` mock) with only config differing.
- `backend/tests/design/test_figma_capabilities.py` — unsupported op degrades cleanly.

## Manual verification steps

1. Set active provider = `figma` → run a design intent → artifact produced.
2. Flip provider stitch↔figma via config → identical handler path, both work.

## Rollback / cleanup notes

- Toggle provider via config. Reversible.

## Design notes

- Where Figma lacks a Stitch-equivalent capability, the adapter reports it via `capabilities()` and the handler/UI adapt — pluggability includes **capability differences**, not just a drop-in swap.

## Status

⬜ todo
