# Phase 49 — PWA finalize

## Title & ID

- **ID:** `phase-49`
- **Title:** PWA finalize
- **Epic:** Epic 11 — Hardening, PWA polish & Demo
- **Milestone:** M5

## Goal

Production-quality PWA: polished offline shell, a sound caching strategy, an update flow, and cross-platform installability.

## Context

Completes D14 (ForgeFlow itself is a PWA). Hardens the baseline from `phase-04` into something that passes audits and behaves well offline/on update — without masking live API/data (which must stay network-first).

## Depends on

- **Phase IDs:** `phase-04`
- **External:** —

### Prerequisite check

- `phase-04` ✅ (PWA base: manifest, icons, SW, offline shell).

## Detailed tasks

1. **Caching strategy (Workbox).** App shell + static assets **precached**; API/data/WS **network-first / network-only** (never serve stale project data); sensible runtime caching for safe GETs; explicit no-cache for auth/secret endpoints.
2. **Update flow.** Detect a new SW → non-intrusive "Update available" prompt → skip-waiting + reload; version surfaced in the UI.
3. **Offline shell polish.** Branded offline fallback; clarify which features need connectivity (most of ForgeFlow is live).
4. **Installability across platforms.** Verify install on Chrome/Edge/Android + iOS Safari add-to-home; correct icons (incl. maskable) + theme colors; splash.
5. **Audits.** Lighthouse PWA pass; fix regressions.

## Out of scope for this phase

- Offline *editing* of projects (out of scope — the platform is inherently online). New features.

## Acceptance criteria

- Installable across target platforms; **passes PWA audits** (Lighthouse installable + best practices).
- Offline reload serves a branded shell; **live data is never served stale** (network-first/only for API/WS).
- SW update prompt works (new version → prompt → reload → updated).

## Tests to write

- `frontend/src/pwa/caching.test.ts` — API/data routes are network-first/only; shell precached (mock Workbox).
- `frontend/src/pwa/updateFlow.test.tsx` — update prompt + reload path.
- A CI Lighthouse/manifest check (installable) where feasible.

## Manual verification steps

1. Install on desktop + mobile → launches standalone with correct icon/splash.
2. Go offline → reload → branded shell (not a browser error).
3. Ship a new build → "Update available" → reload → new version.

## Rollback / cleanup notes

- If SW caching misbehaves, ship a kill-switch SW (unregister) and clear caches; document the recovery. Reversible.

## Design notes

- **Network-first for all live data** is the guiding rule — a builder platform must never show stale project/deploy/test state from cache; the SW accelerates the shell only.

## Status

⬜ todo
