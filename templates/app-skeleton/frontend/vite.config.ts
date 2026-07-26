/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Vite + Vitest config. The dev server host/port are supplied on the command line by ForgeFlow's
// preview (phase-15): `vite --host 0.0.0.0 --port <n>`. Vitest config lives here too so unit tests
// share the app's module resolution.

// The preview proxies to this dev server under a per-project hostname and forwards the original
// Host header. Vite >= 5.4.12 answers 403 to a Host it does not recognise, so the platform passes
// the hostnames it will use in VITE_ALLOWED_HOSTS. (`*.localhost` is always allowed by Vite, which
// is why local preview works without this; a real PREVIEW_BASE_DOMAIN would not.)
const allowedHosts = (process.env.VITE_ALLOWED_HOSTS ?? '')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean)

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    ...(allowedHosts.length > 0 ? { allowedHosts } : {}),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Playwright specs live under e2e/ and are run by `test:e2e`, never by Vitest.
    exclude: ['**/node_modules/**', '**/dist/**', 'e2e/**'],
  },
})
