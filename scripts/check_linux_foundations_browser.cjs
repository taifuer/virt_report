/* Reuse the article shell, diagrams and keyboard viewer at desktop/mobile sizes. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.LINUX_SCREENSHOT_DIR || '/tmp/virt-linux-browser';
const slugs = ['linux-kernel-evolution', 'linux-execution-scheduling', 'linux-memory', 'linux-interrupts', 'linux-block-io', 'linux-network-io'];
(async () => {
  fs.mkdirSync(shots, {recursive: true});
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext();
    await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort());
    const page = await context.newPage(), errors = [], links = new Set();
    page.on('pageerror', error => errors.push(String(error)));
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      for (const slug of slugs) {
        const response = await page.goto(`${base}/mechanisms/${slug}.html`);
        assert.equal(response.status(), 200);
        assert.equal(await page.locator('.mechanism-section').count(),
          ['linux-execution-scheduling', 'linux-memory'].includes(slug) ? 10 : 8);
        if (slug === 'linux-execution-scheduling') {
          assert.equal(await page.locator('h1').innerText(), 'Linux 进程：执行与调度');
        }
        const history = page.locator(slug === 'linux-kernel-evolution' ? '#evolution' : '#history');
        assert.equal(await history.locator('tbody tr').count(), 4);
        await history.scrollIntoViewIfNeeded();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${slug}/${width}: history overflow`);
        await page.screenshot({path: path.join(shots, `${slug}-history-${width}.png`)});
        const example = page.locator(slug === 'linux-kernel-evolution' ? '#request' : slug === 'linux-execution-scheduling' ? '#policy' :
          slug === 'linux-memory' ? '#faults' : slug === 'linux-block-io' ? '#read' :
          slug === 'linux-interrupts' ? '#completion' : '#tx');
        await example.scrollIntoViewIfNeeded();
        assert(await example.locator('h3').count() > 0, `${slug}: concrete mechanism explanation`);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${slug}/${width}: example overflow`);
        await example.screenshot({path: path.join(shots, `${slug}-example-${width}.png`)});
        const expanded = slug === 'linux-execution-scheduling' ? ['objects', 'lifecycle'] :
          slug === 'linux-memory' ? ['mappings', 'pressure'] : [];
        for (const id of expanded) {
          const section = page.locator(`#${id}`);
          await section.scrollIntoViewIfNeeded();
          assert(await section.isVisible(), `${slug}: ${id} readable`);
          assert(await page.locator(`.mechanism-toc a[href="#${id}"]`).count() > 0, `${id}: in navigation`);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${slug}/${width}/${id}: overflow`);
          await section.screenshot({path: path.join(shots, `${slug}-${id}-${width}.png`)});
        }
        const figures = page.locator('.mechanism-diagram');
        assert.equal(await figures.count(), slug === 'linux-kernel-evolution' ? 4 : 3);
        for (const figure of await figures.all()) {
          const name = await figure.getAttribute('data-diagram');
          await figure.scrollIntoViewIfNeeded();
          await page.waitForFunction(name => {
            const img = document.querySelector(`[data-diagram="${name}"] img`);
            return img.complete && img.naturalWidth > 0;
          }, name);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${slug}/${width}: overflow`);
          await figure.screenshot({path: path.join(shots, `${name}-${width}.png`)});
          const open = figure.locator('[data-mechanism-expand]');
          await open.focus(); await page.keyboard.press('Enter');
          const dialog = page.locator('.mechanism-viewer[open]');
          await dialog.waitFor();
          await dialog.locator('.mechanism-viewer-image').waitFor({state: 'visible'});
          await dialog.locator('[data-viewer-action="actual"]').click();
          assert.equal(await dialog.locator('output').innerText(), '100%');
          await page.keyboard.press('Escape');
          await dialog.waitFor({state: 'detached'});
          assert(await open.evaluate(node => node === document.activeElement));
        }
        if (width === 1440) {
          for (const href of await page.locator('.mechanism-section a[href]').evaluateAll(nodes => nodes.map(n => n.href))) {
            const url = new URL(href);
            if (url.origin === new URL(base).origin && url.pathname.endsWith('.html')) links.add(url.href.split('#')[0]);
          }
        }
      }
    }
    for (const url of links) assert.equal((await context.request.get(url)).status(), 200, url);
    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS Linux foundations: six articles, 19 diagrams, three widths, SVG loading, keyboard viewer, local links');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
