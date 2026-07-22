#!/usr/bin/env node
// PWA installability + caching gate (phase-49).
//
// A full Lighthouse audit needs a headless browser and a served build — too heavy for every CI run.
// This validates the *outputs* a passing audit depends on, deterministically and in milliseconds:
// the built manifest has the installability fields, and the generated service worker actually
// implements the "network-first for live data, precache the shell" strategy. Full Lighthouse stays
// a manual pre-release step (see docs/hardening-checklist.md).
//
// Usage: `node scripts/check-pwa.mjs [distDir]` (defaults to ./dist). Exits non-zero on any failure.

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const dist = process.argv[2] ?? 'dist';
const errors = [];
const ok = (msg) => console.log(`  ok   ${msg}`);
const fail = (msg) => {
  errors.push(msg);
  console.log(`  FAIL ${msg}`);
};

// --- manifest ---------------------------------------------------------------------------------
const manifestPath = join(dist, 'manifest.webmanifest');
if (!existsSync(manifestPath)) {
  fail(`manifest not found at ${manifestPath} — run \`vite build\` first`);
} else {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));

  for (const field of [
    'name',
    'short_name',
    'start_url',
    'display',
    'theme_color',
    'background_color',
  ]) {
    if (manifest[field]) ok(`manifest.${field} = ${JSON.stringify(manifest[field])}`);
    else fail(`manifest is missing required field: ${field}`);
  }

  if (manifest.display === 'standalone') ok('manifest.display is standalone');
  else fail(`manifest.display must be "standalone" (got ${manifest.display})`);

  const icons = manifest.icons ?? [];
  const sizes = new Set(icons.map((i) => i.sizes));
  for (const size of ['192x192', '512x512']) {
    if (sizes.has(size)) ok(`icon ${size} present`);
    else fail(`missing required icon size: ${size}`);
  }
  if (icons.some((i) => (i.purpose ?? '').includes('maskable'))) ok('a maskable icon is declared');
  else fail('no maskable icon — Android adaptive icons will clip the logo');

  // Every icon the manifest promises must actually exist in the build.
  for (const icon of icons) {
    if (existsSync(join(dist, icon.src))) ok(`icon file present: ${icon.src}`);
    else fail(`manifest references a missing icon file: ${icon.src}`);
  }
}

// --- service worker ---------------------------------------------------------------------------
const swCandidates = ['sw.js', 'service-worker.js'].filter((f) => existsSync(join(dist, f)));
if (swCandidates.length === 0) {
  fail(`no service worker found in ${dist} (expected sw.js)`);
} else {
  const sw = readFileSync(join(dist, swCandidates[0]), 'utf8');
  ok(`service worker present: ${swCandidates[0]}`);

  // Live data must be network-first/only — never served stale from cache.
  if (/NetworkOnly|NetworkFirst/.test(sw))
    ok('SW uses a network-first/only strategy for live data');
  else fail('SW has no NetworkOnly/NetworkFirst rule — live data could be served stale');

  // The offline shell: a navigation route that serves the precached index.
  if (/NavigationRoute|navigateFallback|createHandlerBoundToURL/.test(sw)) {
    ok('SW serves an app-shell navigation fallback (offline shell)');
  } else {
    fail('SW has no navigation fallback — offline reload would fail');
  }

  // The precache manifest must have real entries (the shell was actually precached).
  if (/precacheAndRoute\(\[?\{/.test(sw) || /"revision"/.test(sw)) ok('SW precaches the app shell');
  else fail('SW precache manifest looks empty');
}

// --- summary ----------------------------------------------------------------------------------
const assets = existsSync(dist) ? readdirSync(dist).length : 0;
console.log(`\nChecked ${dist} (${assets} entries).`);
if (errors.length) {
  console.error(`\nPWA check failed with ${errors.length} problem(s).`);
  process.exit(1);
}
console.log('\nPWA installability + caching checks passed.');
