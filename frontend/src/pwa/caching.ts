// Service-worker caching strategy (phase-49, D14).
//
// The guiding rule: **network-first for all live data**. ForgeFlow is a builder platform — showing
// a cached, stale view of project / deploy / test state would be actively misleading, so the
// service worker accelerates the *app shell* and nothing else. Live data (the API, on its own
// origin) is network-only; auth and credential endpoints are never cached under any circumstances.
//
// This module is pure config so it can be unit-tested (caching.test.ts) without running Workbox,
// and is consumed by vite.config.ts at build time.

/** The Workbox strategies we use (a structural subset — workbox-build's type isn't resolvable). */
export type WorkboxStrategy =
  'NetworkOnly' | 'NetworkFirst' | 'CacheFirst' | 'StaleWhileRevalidate' | 'CacheOnly';

export interface RuntimeCachingRule {
  urlPattern: RegExp;
  handler: WorkboxStrategy;
  options?: {
    cacheName?: string;
    expiration?: { maxEntries?: number; maxAgeSeconds?: number };
    networkTimeoutSeconds?: number;
    cacheableResponse?: { statuses: number[] };
  };
}

export const DEFAULT_API_BASE_URL = 'http://localhost:8000';

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Endpoints whose responses must never be written to a cache, even opportunistically. */
const NEVER_CACHE_PATHS = ['auth', 'credentials'] as const;

/**
 * Build the runtime-caching rules for a given API origin.
 *
 * The API is a separate origin (`VITE_API_BASE_URL`), so these rules target it explicitly:
 * everything under it is `NetworkOnly` (never cached → never stale), with auth/secret endpoints
 * called out first so the intent is unmistakable and independently testable. WebSocket traffic
 * (`ws(s)://`) is not a `fetch` and the service worker never sees it — it is network-only by nature.
 */
export function buildRuntimeCaching(
  apiBaseUrl: string = DEFAULT_API_BASE_URL,
): RuntimeCachingRule[] {
  const origin = safeOrigin(apiBaseUrl);
  const originPrefix = escapeRegExp(origin);

  return [
    {
      // Credentials and auth: never cached, ever — a stale/persisted token is a security problem.
      urlPattern: new RegExp(`^${originPrefix}/(?:${NEVER_CACHE_PATHS.join('|')})`),
      handler: 'NetworkOnly',
      options: { cacheName: 'ff-no-store' },
    },
    {
      // All other live data (projects, stages, deploys, tests, runs, …): never stale.
      urlPattern: new RegExp(`^${originPrefix}/`),
      handler: 'NetworkOnly',
      options: { cacheName: 'ff-api' },
    },
  ];
}

/**
 * Navigations to these paths must NOT be answered with the precached app shell. A SPA serves the
 * shell for real routes; but a request that looks like an API or socket path should fall through to
 * the network so the user gets a real response (or a real network error), never a misleading shell.
 */
export const navigateFallbackDenylist: RegExp[] = [
  /^\/api\//,
  /^\/ws\b/,
  /^\/auth\b/,
  /^\/credentials\b/,
  // Anything with a file extension is an asset, not an app route.
  /\/[^/?]+\.[^/?]+$/,
];

/** Precache globs for the app shell + static assets (woff2: the self-hosted Inter/JetBrains). */
export const PRECACHE_GLOBS = ['**/*.{js,css,html,svg,png,ico,webmanifest,woff2}'];

/**
 * Kept out of the precache: Monaco's language workers are huge (ts.worker alone is ~6 MB — the
 * TypeScript compiler) and the IDE cannot work offline anyway (it reads/writes the sandbox over the
 * API), so precaching them would cost megabytes at install for zero offline benefit.
 */
export const PRECACHE_IGNORES = ['**/*.worker-*.js', '**/Ide-*.js'];

function safeOrigin(apiBaseUrl: string): string {
  try {
    return new URL(apiBaseUrl).origin;
  } catch {
    return new URL(DEFAULT_API_BASE_URL).origin;
  }
}
