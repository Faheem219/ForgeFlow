import { defineConfig, devices } from '@playwright/test'

// Playwright E2E config. Two ways it runs:
//   1. Dev/preview & CI — against a booted app; set PLAYWRIGHT_BASE_URL to the FE preview URL.
//   2. Live validation (phase-39) — PLAYWRIGHT_BASE_URL points at the deployed URL.
// Chromium is pre-installed in the sandbox image (PLAYWRIGHT_BROWSERS_PATH=/ms-playwright), so no
// `playwright install` is needed there. `webServer` boots the app locally when no base URL is set.
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  // One browser at a time. Playwright sizes its worker pool from the *host's* CPU count, not the
  // sandbox's 1-CPU cap, and every Chromium costs ~100 threads against the sandbox's pid limit
  // (256): two workers peaked at 247, three crashed the browsers mid-launch ("pthread_create:
  // Resource temporarily unavailable"), failing every e2e test for a reason the code never caused.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'pnpm dev --port 5173',
        url: 'http://localhost:5173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
})
