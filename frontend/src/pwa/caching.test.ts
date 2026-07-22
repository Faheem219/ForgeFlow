import { describe, expect, it } from 'vitest';

import {
  DEFAULT_API_BASE_URL,
  PRECACHE_GLOBS,
  PRECACHE_IGNORES,
  buildRuntimeCaching,
  navigateFallbackDenylist,
  type RuntimeCachingRule,
} from './caching';

const API = 'https://api.forgeflow.app';

function matching(rules: RuntimeCachingRule[], url: string): RuntimeCachingRule | undefined {
  // Workbox applies the first rule whose pattern matches — mirror that here.
  return rules.find((r) => r.urlPattern.test(url));
}

describe('runtime caching strategy', () => {
  it('serves live API data network-only, so project state is never stale', () => {
    const rules = buildRuntimeCaching(API);

    for (const path of ['/projects', '/projects/abc/stages', '/projects/abc/deploy/latest']) {
      const rule = matching(rules, `${API}${path}`);
      expect(rule, path).toBeDefined();
      expect(rule?.handler).toBe('NetworkOnly');
    }
  });

  it('never caches auth or credential endpoints', () => {
    const rules = buildRuntimeCaching(API);

    for (const path of ['/auth/login', '/auth/register', '/credentials', '/credentials/vercel']) {
      const rule = matching(rules, `${API}${path}`);
      expect(rule?.handler).toBe('NetworkOnly');
      // ...and they resolve to the dedicated no-store cache bucket, not the general API one.
      expect(rule?.options?.cacheName).toBe('ff-no-store');
    }
  });

  it('has no rule that would cache an API response (nothing but NetworkOnly)', () => {
    const rules = buildRuntimeCaching(API);
    expect(rules.length).toBeGreaterThan(0);
    expect(rules.every((r) => r.handler === 'NetworkOnly')).toBe(true);
  });

  it('does not intercept the app origin (only the API origin is matched)', () => {
    const rules = buildRuntimeCaching(API);
    for (const appUrl of ['https://app.forgeflow.app/', 'https://app.forgeflow.app/projects/x']) {
      expect(matching(rules, appUrl)).toBeUndefined();
    }
  });

  it('falls back to the default API origin when the base URL is missing or invalid', () => {
    const fromDefault = buildRuntimeCaching(undefined);
    const rule = matching(fromDefault, `${DEFAULT_API_BASE_URL}/projects`);
    expect(rule?.handler).toBe('NetworkOnly');

    const fromGarbage = buildRuntimeCaching('not a url');
    expect(matching(fromGarbage, `${DEFAULT_API_BASE_URL}/projects`)?.handler).toBe('NetworkOnly');
  });

  it('escapes regex metacharacters in the origin so matching stays exact', () => {
    // A dot in the host must match a literal dot, not "any character".
    const rules = buildRuntimeCaching('https://api.example.com');
    expect(matching(rules, 'https://apiXexample.com/projects')).toBeUndefined();
  });
});

describe('navigateFallbackDenylist', () => {
  it('keeps the app shell from answering API, socket and asset requests', () => {
    for (const path of ['/api/x', '/ws', '/ws/projects/1', '/auth/login', '/assets/app-abc.js']) {
      expect(navigateFallbackDenylist.some((re) => re.test(path))).toBe(true);
    }
  });

  it('still serves the shell for real app routes', () => {
    for (const route of ['/', '/projects/abc', '/settings', '/eval']) {
      expect(navigateFallbackDenylist.some((re) => re.test(route))).toBe(false);
    }
  });
});

describe('precache configuration', () => {
  it('precaches the shell + static assets', () => {
    expect(PRECACHE_GLOBS.join(' ')).toMatch(/js.*css.*html|html.*js/);
  });

  it('keeps the heavy Monaco chunks out of the precache', () => {
    expect(PRECACHE_IGNORES).toContain('**/*.worker-*.js');
    expect(PRECACHE_IGNORES.some((g) => g.includes('Ide-'))).toBe(true);
  });
});
