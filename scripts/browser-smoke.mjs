import {chromium, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import config from '../observablehq.config.js';

const base = process.env.GPA_TEST_URL ?? 'http://127.0.0.1:3000/';
const screenshotDir = process.env.GPA_SCREENSHOT_DIR ?? 'docs/screenshots';
// Follow navigation so a newly added page cannot silently miss the smoke test.
const routes = ['', ...config.pages.map(({path}) => path.replace(/^\//, ''))];
const textOnlyRoutes = new Set(['methodology']);
// Plot's generated class distinguishes charts from SVG legend swatches.
const chartSelector = 'main svg[class*="plot-"]';
const browser = await chromium.launch({channel: process.env.CI ? undefined : 'chrome', headless: true});
const errors = [];
try {
  await mkdir(screenshotDir, {recursive: true});
  for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
    const page = await browser.newPage({viewport});
    page.on('pageerror', error => errors.push(JSON.stringify({url: page.url(), width: viewport.width, error: error.message})));
    for (const route of routes) {
      const response = await page.goto(new URL(route, base).href, {waitUntil: 'networkidle'});
      if (!response?.ok()) errors.push(JSON.stringify({route, width: viewport.width, status: response?.status()}));
      if (!textOnlyRoutes.has(route)) {
        try {
          await expect(page.locator(chartSelector).first()).toBeVisible({timeout: 10000});
        } catch {
          errors.push(JSON.stringify({route, width: viewport.width, error: 'No visible Plot chart'}));
        }
      }
      if (route === 'battery') {
        // A different chart on the page must not hide a blank cumulative plot.
        try {
          const cumulative = page.locator('#battery-cumulative');
          await expect(cumulative).toBeVisible({timeout: 10000});
          await expect(cumulative.locator('g[aria-label="line"] path')).toHaveCount(7);
          for (const id of ['battery-scoreboard', 'battery-comparisons', 'battery-costs', 'battery-sensitivities', 'battery-risk', 'battery-durations']) {
            const table = page.locator(`#${id}`);
            await expect(table).toBeVisible();
            await expect(table.locator('tbody tr').first()).toBeVisible();
          }
          await expect(page.locator('#battery-scoreboard')).toContainText('Similar day');
          await expect(page.locator('#battery-scoreboard')).toContainText('Previous day');
          await expect(page.locator('#battery-sensitivities')).toContainText('85%');
          await page.locator('main select').first().selectOption({index: 0});
          await expect(cumulative).toContainText('1h battery');
          await expect(cumulative.locator('g[aria-label="line"] path')).toHaveCount(7);
          await page.locator('main select').first().selectOption({index: 2});
          await expect(cumulative).toContainText('4h battery');
          await expect(page.locator('main')).toContainText('Is this margin durable as the market changes?');
          await expect(page.locator('main')).toContainText('solar cannibalisation');
          await expect(page.locator('main')).not.toContainText('NaN');
          await expect(page.locator('main')).not.toContainText('undefined');
        } catch (error) {
          errors.push(JSON.stringify({route, width: viewport.width, error: `Battery evidence: ${error.message}`}));
        }
      }
      await page.waitForTimeout(1200);
      const report = await page.evaluate((selector) => ({
        title: document.querySelector('main h1')?.textContent,
        errors: [...document.querySelectorAll('.observablehq--error')].map(e => e.textContent),
        charts: document.querySelectorAll(selector).length,
        overflow: document.documentElement.scrollWidth > innerWidth + 2,
      }), chartSelector);
      if (report.errors.length || report.overflow || !report.title) errors.push(JSON.stringify({route, width: viewport.width, ...report}));
      console.log(JSON.stringify({route: route || 'index', width: viewport.width, ...report}));
      for (const select of await page.locator('main select').all()) {
        if (await select.locator('option').count() > 1) await select.selectOption({index: 1});
      }
      await page.waitForTimeout(200);
      const after = await page.locator('.observablehq--error').allTextContents();
      errors.push(...after);
      if (!route) await page.screenshot({path: join(screenshotDir, `dashboard-${viewport.width}.png`), fullPage: true});
    }
    await page.close();
  }
} finally {
  await browser.close();
}
if (errors.length) { console.error(JSON.stringify(errors, null, 2)); process.exit(1); }
