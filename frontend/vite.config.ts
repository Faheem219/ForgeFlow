import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';

import {
  PRECACHE_GLOBS,
  PRECACHE_IGNORES,
  buildRuntimeCaching,
  navigateFallbackDenylist,
} from './src/pwa/caching';
import { pwaManifest } from './src/pwa/manifest';

const appVersion = process.env.npm_package_version ?? '0.0.0';

// https://vitejs.dev/config/ — Vitest config lives under `test`.
export default defineConfig({
  define: {
    // Surfaced in the UI so a user can tell which build they're running (phase-49 update flow).
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    react(),
    VitePWA({
      registerType: 'prompt',
      disable: process.env.VITEST !== undefined, // no SW during unit tests
      manifest: pwaManifest,
      includeAssets: ['favicon.svg', 'apple-touch-icon-180.png'],
      workbox: {
        // The app shell is served offline; live data is not (see src/pwa/caching.ts).
        navigateFallback: 'index.html',
        navigateFallbackDenylist,
        globPatterns: PRECACHE_GLOBS,
        globIgnores: PRECACHE_IGNORES,
        // Network-first/only for the API origin — a builder must never show stale project state.
        runtimeCaching: buildRuntimeCaching(process.env.VITE_API_BASE_URL),
        // A new SW takes over only when the user accepts the update prompt (registerType: prompt).
        clientsClaim: false,
        skipWaiting: false,
      },
    }),
  ],
  server: {
    host: true,
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
