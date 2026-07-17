import { expect, test } from '@playwright/test'

// Example E2E — proves Playwright is wired up. It runs against a booted preview (dev/CI) or the
// live deployment (phase-39). Generated E2E specs are added alongside this file.
test('home page renders', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /your app starts here/i })).toBeVisible()
})
