// Optional measurement: requires Playwright and a local synthetic Cardine server.
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: process.argv[3] === 'mobile'
      ? { width: 390, height: 844 } : { width: 1440, height: 900 } });
    await page.goto(process.argv[2]);
    await page.waitForFunction(() => document.querySelector('#rail-course')?.textContent.includes('Recall'));
    await page.evaluate(() => document.querySelector('[data-route="ripasso"]').click());
    await page.locator('[data-reveal-review]').click();
    const refresh = page.waitForResponse(r => r.url().endsWith('/bootstrap'));
    const response = page.waitForResponse(r => r.url().endsWith('/reviews'));
    const metric = await page.evaluate(async () => {
      const start = performance.now();
      const before = document.querySelector('.review-card__front').textContent;
      document.querySelector('[data-command="review"][data-rating="good"]').click();
      let visible = null;
      while (performance.now() - start < 10000) {
        const front = document.querySelector('.review-card__front');
        if (front && front.textContent !== before) {
          visible ??= performance.now() - start;
          const reveal = document.querySelector('[data-reveal-review]');
          if (reveal && !reveal.disabled) reveal.click();
          const rating = document.querySelector('[data-command="review"]');
          if (rating && !rating.disabled) {
            // Two frame boundaries include a rendering opportunity, rather
            // than reporting only the synchronous DOM mutation duration.
            await new Promise(requestAnimationFrame);
            await new Promise(requestAnimationFrame);
            return { visibleMs: visible, clickToPaintAndInteractiveMs: performance.now() - start,
              pendingAtNextCard: !!document.querySelector('[data-review-pending]') };
          }
        }
        await new Promise(requestAnimationFrame);
      }
      throw Error('next card not interactive');
    });
    const receipt = await (await response).json();
    if (receipt.status !== 'committed' || !receipt.next_schedule) throw Error('canonical review receipt missing');
    await page.locator('[data-review-pending]').waitFor({ state: 'detached' });
    await (await refresh).finished();
    console.log(JSON.stringify({ ...metric, committed: true, nextSchedule: true }));
  } finally { await browser.close(); }
})();
