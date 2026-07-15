import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vitest/config';

import { pwaManifest } from './src/pwa/manifest';

// https://vitejs.dev/config/ — Vitest config lives under `test`.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'prompt',
      disable: process.env.VITEST !== undefined, // no SW during unit tests
      manifest: pwaManifest,
      includeAssets: ['favicon.svg', 'apple-touch-icon-180.png'],
      workbox: {
        // App-shell offline fallback; conservative caching (phase-49 hardens this).
        navigateFallback: 'index.html',
        globPatterns: ['**/*.{js,css,html,svg,png,ico,webmanifest}'],
        // Keep Monaco out of the precache. Its language workers are large (ts.worker alone is
        // ~6 MB — it's the TypeScript compiler), and the IDE can't work offline regardless: it
        // reads and writes the sandbox over the API. Precaching it would make the service-worker
        // install cost megabytes for no offline benefit, so these chunks load on demand instead.
        // The app shell stays precached, which is where the offline value actually is.
        globIgnores: ['**/*.worker-*.js', '**/Ide-*.js'],
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
