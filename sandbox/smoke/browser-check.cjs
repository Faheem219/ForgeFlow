// Playwright (chromium) smoke check: launch the pre-installed browser, load the sample app, and
// assert its content. `require('playwright')` resolves via NODE_PATH=/opt/pw/node_modules (CommonJS).
const { chromium } = require('playwright');

const PORT = Number(process.env.SMOKE_PORT || 3123);
const URL = `http://127.0.0.1:${PORT}/`;

(async () => {
  // The Docker container is the isolation boundary, so chromium's own setuid sandbox is disabled
  // here (it can't run in an unprivileged container without extra caps/seccomp).
  const browser = await chromium.launch({ chromiumSandbox: false });
  try {
    const page = await browser.newPage();
    await page.goto(URL, { waitUntil: 'domcontentloaded' });
    const text = (await page.textContent('#status')) ?? '';
    if (text.trim() !== 'ok') {
      throw new Error(`unexpected #status content: ${JSON.stringify(text)}`);
    }
    console.log('playwright-ok');
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error('playwright-check failed:', err);
  process.exit(1);
});
