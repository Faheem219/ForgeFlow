import { expect, test } from '@playwright/test'

// Example E2E — proves Playwright is wired up. It runs against a booted preview (dev/CI) or the
// live deployment (phase-39). Generated E2E specs are added alongside this file.
//
// It asserts the app MOUNTS, deliberately not what it says. An earlier version asserted the
// skeleton's placeholder heading, which made this spec unsatisfiable the moment a real app was
// generated: the build's placeholder gate (build_verify.py) *fails* a build that still contains
// that copy, so the demo text and this assertion could never both hold. The repair loop cannot
// resolve that — it is barred from editing test files (agents/repair.py `is_test_file`) — so it
// burned its whole budget rewriting pages that were never the problem. Keep this smoke test
// content-agnostic: it must pass on the bare skeleton AND on any app generated from it.
test('home page renders', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('#root')).not.toBeEmpty()
})
