import {chromium} from '@playwright/test';
import {mkdir} from 'node:fs/promises';

const base = process.env.GPA_TEST_URL ?? 'http://127.0.0.1:3000/';
const browser = await chromium.launch({channel: process.env.CI ? undefined : 'chrome', headless: true});
const errors = [];
await mkdir('docs/screenshots', {recursive: true});
for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
  const page = await browser.newPage({viewport});
  page.on('pageerror', error => errors.push(error.message));
  for (const route of ['', 'prices', 'demand', 'supply', 'spreads', 'methodology']) {
    await page.goto(new URL(route, base).href, {waitUntil: 'networkidle'});
    await page.waitForTimeout(1200);
    const report = await page.evaluate(() => ({
      title: document.querySelector('main h1')?.textContent,
      errors: [...document.querySelectorAll('.observablehq--error')].map(e => e.textContent),
      charts: document.querySelectorAll('main svg').length,
      overflow: document.documentElement.scrollWidth > innerWidth + 2,
    }));
    if (report.errors.length || report.overflow || !report.title) errors.push(JSON.stringify({route, width: viewport.width, ...report}));
    console.log(JSON.stringify({route: route || 'index', width: viewport.width, ...report}));
    for (const select of await page.locator('main select').all()) {
      if (await select.locator('option').count() > 1) await select.selectOption({index: 1});
    }
    await page.waitForTimeout(200);
    const after = await page.locator('.observablehq--error').allTextContents();
    errors.push(...after);
    if (!route) await page.screenshot({path: `docs/screenshots/dashboard-${viewport.width}.png`, fullPage: true});
  }
  await page.close();
}
await browser.close();
if (errors.length) { console.error(JSON.stringify(errors, null, 2)); process.exit(1); }
