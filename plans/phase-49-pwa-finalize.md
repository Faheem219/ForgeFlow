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

✅ done

**Implementation notes**

- **Caching strategy** — [caching.ts](../frontend/src/pwa/caching.ts) (pure config, so it's unit-testable): the app shell + static assets are **precached**; the API origin is **NetworkOnly** (live project/deploy/test state is never cached → never stale); auth + credential endpoints route to a dedicated `ff-no-store` bucket; Monaco/IDE chunks stay out of the precache (~6 MB, useless offline). A `navigateFallbackDenylist` stops API/socket/asset-shaped paths from being answered with the shell. Verified baked into the built `sw.js` (two `NetworkOnly` `registerRoute`s for the API origin + a `NavigationRoute` for the offline shell).
- **Update flow** — kept `registerType: 'prompt'` (a new SW never activates silently): `onNeedRefresh` → persistent "Update available" toast → **Reload** calls `updateSW(true)`. The build version is surfaced in the sidebar footer (`data-testid="app-version"`, via a `__APP_VERSION__` Vite define) so a user can confirm an update took effect.
- **Offline shell** — `navigateFallback: index.html` serves the branded shell on offline reload; live features honestly fail with the phase-48 "Can't reach the server" message rather than faking cached data.
- **Installability** — manifest hardened with `id`, `lang`, `dir`, `orientation`, `categories`, `any`+`maskable` icons; iOS add-to-home via `apple-touch-icon-180`.
- **Audit** — [scripts/check-pwa.mjs](../frontend/scripts/check-pwa.mjs) (`pnpm run check:pwa`, wired into CI after build): validates the built manifest's installability fields, that every referenced icon file exists (caught a missing 192 maskable during development), and that the generated SW implements network-first-for-live-data + a navigation fallback. Full Lighthouse stays a documented manual pre-release step (needs a headless browser). Recovery via a kill-switch SW is documented in [docs/pwa.md](../docs/pwa.md).
- **Error boundary** — the phase-48 boundary already covers a render crash; nothing PWA-specific needed.

**Tests:** `caching.test.ts` (11 — network-only for the API/auth, app origin untouched, denylist, precache globs, invalid-base-URL fallback, regex escaping) + `updateFlow.test.tsx` (5 — prompt appears, Reload → `updateSW(true)`, immediate registration, offline-ready, no nag on first load). Frontend 251 passed (18 PWA tests all green); tsc/eslint/prettier clean; `vite build` + `check:pwa` pass. The one red is the pre-existing phase-45 chart test that assumes US number grouping (fails only under this machine's `en-IN` locale). Deferred (need a device/browser): the install-on-desktop-and-mobile and new-build-update manual walkthroughs, and a full Lighthouse run.
