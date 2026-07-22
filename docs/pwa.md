# ForgeFlow — PWA notes

ForgeFlow is itself an installable PWA (D14). This documents the caching strategy, the update flow,
what works offline, and — most importantly — how to recover if the service worker ever misbehaves.

Config lives in [`frontend/src/pwa/`](../frontend/src/pwa/) (`manifest.ts`, `caching.ts`,
`registerPwa.ts`) and is wired up in `vite.config.ts`. The build is gated by
`frontend/scripts/check-pwa.mjs` (run in CI as `pnpm run check:pwa`).

## Caching strategy — network-first for all live data

The guiding rule: **the service worker accelerates the app shell and nothing else.** A builder
platform must never show a cached, stale view of project / deploy / test state.

| What | Strategy | Why |
|------|----------|-----|
| App shell + static assets (JS/CSS/HTML/icons) | **Precache** | Instant load; the only thing that's safe to serve offline |
| API — all of it (`VITE_API_BASE_URL` origin) | **NetworkOnly** | Live data is never cached → never stale |
| Auth + credentials endpoints | **NetworkOnly** (dedicated `ff-no-store` bucket) | A persisted token would be a security problem |
| WebSocket (`ws(s)://`) | Network-only by nature | The SW intercepts `fetch`, not sockets |
| Monaco language workers, the IDE chunk | **Not precached** | ~6 MB (the TS compiler); the IDE can't work offline anyway (it talks to the sandbox over the API) |

The API is a **separate origin**, so these rules target it explicitly. Same-origin navigations fall
back to the precached `index.html`, except paths on the `navigateFallbackDenylist` (API/socket/asset
shapes) which fall through to the network so the user never sees a misleading shell.

## Update flow

`registerType: 'prompt'` — a new service worker never activates silently.

1. A new build is deployed; the browser fetches the new `sw.js` and it enters the *waiting* state.
2. `registerPwa` (via `onNeedRefresh`) raises a persistent **"Update available"** toast with a
   **Reload** action.
3. Clicking Reload calls `updateSW(true)` → skip-waiting + reload onto the new version.
4. The running build's version is shown in the sidebar footer (`data-testid="app-version"`), so a
   user can confirm the update actually took effect.

## Offline behaviour

- **Offline reload** serves the branded app shell (not a browser error page).
- **Live features do not work offline** — and shouldn't fake it. The realtime indicator in the
  header shows `offline`, and API calls fail with the phase-48 "Can't reach the server" message.
- Offline *editing* of projects is explicitly out of scope: ForgeFlow is inherently online.

## Installability

The manifest ships `name`, `short_name`, `id`, `start_url`, `scope`, `display: standalone`,
`theme_color`, `background_color`, `orientation`, `categories`, and icons at 192/512 plus a 512
**maskable** icon (Android adaptive icons). `apple-touch-icon-180.png` covers iOS add-to-home.
`check:pwa` verifies every icon the manifest references actually exists in the build.

**Manual audit (pre-release):** run Lighthouse → PWA category against a served production build
(`pnpm run build && pnpm run preview`). CI runs the deterministic manifest/SW check instead, since a
full Lighthouse pass needs a headless browser.

## Recovery — the service-worker kill switch

If a bad service worker is ever shipped and caching misbehaves (users stuck on a stale shell), the
fix is a **kill-switch SW** that unregisters itself and clears all caches. Deploy this as `sw.js`:

```js
// Kill-switch service worker — replaces a misbehaving one, then gets out of the way.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', async () => {
  const keys = await caches.keys();
  await Promise.all(keys.map((k) => caches.delete(k)));
  await self.registration.unregister();
  const clients = await self.clients.matchAll();
  clients.forEach((c) => c.navigate(c.url)); // reload every open tab onto the network
});
```

Because the app registers with `registerType: 'prompt'`, clients pick up the replacement SW on their
next load; the kill switch then purges caches and unregisters, returning everyone to a clean,
network-served state. After recovery, ship a corrected SW as the next build.
