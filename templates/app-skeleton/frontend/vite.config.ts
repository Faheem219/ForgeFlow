/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Vite + Vitest config. The dev server host/port are supplied on the command line by ForgeFlow's
// preview (phase-15): `vite --host 0.0.0.0 --port <n>`. Vitest config lives here too so unit tests
// share the app's module resolution.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Playwright specs live under e2e/ and are run by `test:e2e`, never by Vitest.
    exclude: ['**/node_modules/**', '**/dist/**', 'e2e/**'],
  },
})
