# Phase 04 — Frontend app shell & PWA base

## Title & ID

- **ID:** `phase-04`
- **Title:** Frontend app shell & PWA base
- **Epic:** Epic 0 — Foundations & Scaffolding
- **Milestone:** M1

## Goal

A navigable app shell (sidebar/topbar, routing, dark indigo/violet theme, base UI kit) that is an installable PWA with an offline app shell.

## Context

Delivers the presentation-plane baseline (D3, D14). ForgeFlow itself is a PWA (D14) that builds regular web apps. This shell hosts the dashboard and workspace in later phases; the UI kit and theme tokens are reused everywhere.

## Depends on

- **Phase IDs:** `phase-01`
- **External:** —

### Prerequisite check

- `phase-01` ✅; frontend boots via `make dev`; Tailwind theme tokens exist.

## Detailed tasks

1. **Router (`src/app/router.tsx`).** React Router routes: `/login`, `/register` (from `phase-03`), `/` (dashboard placeholder), `/projects/:id` (workspace placeholder), `/settings` (placeholder; admin area added in `phase-52`), `*` (404).
2. **Layout (`src/app/layout/`).** `AppShell` with collapsible sidebar (nav: Dashboard, Settings) + topbar (user menu, theme indicator, connection status placeholder for `phase-05`). Responsive; content outlet.
3. **Theme (`src/app/theme/`).** Dark theme, indigo/violet accents; CSS variables/tokens; typography scale; focus-visible + a11y contrast.
4. **UI kit (`src/components/ui/`).** `Button`, `Panel`, `Modal`, `Toast`/`Toaster`, `Input`, `Tabs`, `Spinner`, `EmptyState`, `Badge`. Typed props, `strict`, no `any`. Storybook optional; at minimum a `/_kit` dev route rendering all variants.
5. **PWA (`vite-plugin-pwa` + Workbox).** `public/manifest.webmanifest` (name, theme/background colors, icons 192/512/maskable), offline **app-shell** fallback route, service worker registration with an update prompt hook. Icons under `public/`.
6. **State/query providers.** Mount `QueryClientProvider` (TanStack Query) and toast provider at the root.

## Out of scope for this phase

- Workspace internals & stage panels (`phase-09`), realtime wiring (`phase-05`), final PWA hardening/offline polish (`phase-49`), admin settings screens (`phase-52`).

## Acceptance criteria

- App installs as a PWA (Chrome install prompt / Lighthouse "installable" passes).
- Offline: with the SW active, reloading offline serves the app shell (not a browser error).
- All routes render; theme is consistent; UI-kit page renders every component variant.
- Lint/type/format clean.

## Tests to write

- `frontend/src/components/ui/*.test.tsx` — render + interaction for `Button`, `Modal`, `Toast`.
- `frontend/src/app/router.test.tsx` — unknown route → 404; protected route redirect (uses `phase-03` guard).
- PWA: a manifest presence test + SW registration smoke (mock).

## Manual verification steps

1. `make dev` → navigate all routes; toggle sidebar; open a modal/toast.
2. Chrome DevTools → Application → Manifest valid; "Install" available.
3. DevTools → Network offline → reload → app shell renders.
4. Lighthouse PWA audit → installable check green.

## Rollback / cleanup notes

- Unregister the service worker in DevTools if SW caching interferes during dev. No persistent server state. Reversible.

## Design notes

- Keep the service worker conservative here (app-shell only); aggressive caching strategies are deferred to `phase-49` to avoid masking API changes during development.

## Status

⬜ todo
