/* Local-only checks: article contents, backend alignment and industry headings. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.REVIEW_SCREENSHOT_DIR || '/tmp/virt-review-browser';
const localOnly = route => new URL(route.request().url()).origin === new URL(base).origin
  ? route.continue() : route.abort();
async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
}
async function industryBadge(page) {
  const gap = await page.locator('.industry-title-row').evaluate(async row => {
    await document.fonts.ready;
    const title = row.querySelector('h1'), badge = row.querySelector('a');
    const font = getComputedStyle(title), ctx = document.createElement('canvas').getContext('2d');
    ctx.font = [font.fontWeight, font.fontSize, font.fontFamily].join(' ');
    const descent = ctx.measureText(title.textContent).actualBoundingBoxDescent;
    // A zero-height inline block marks the text baseline, excluding the line's leading.
    const baseline = document.createElement('span');
    baseline.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
    title.append(baseline);
    const inkBottom = baseline.getBoundingClientRect().bottom + descent;
    baseline.remove();
    return badge.getBoundingClientRect().bottom - inkBottom;
  });
  assert(Math.abs(gap) <= 3, `badge edge is ${gap}px from the heading ink on ${page.url()}`);
}
(async () => {
  fs.mkdirSync(shots, {recursive: true});
  const browser = await launchBrowser({headless: true});
  try {
    const context = await browser.newContext();
    await context.route('**/*', localOnly);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    for (const width of [1440, 960, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      for (const edition of ['index', 'archive']) {
        await page.goto(`${base}/industry/${edition}.html`);
        await industryBadge(page);
      }
      await page.goto(`${base}/mechanisms/linux-memory.html`);
      const toggle = page.locator('[data-toc-toggle]'), toc = page.locator('#mechanism-toc-links');
      if (width > 960) {
        assert(await toggle.isHidden()); assert(await toc.isVisible());
      } else {
        assert(await toggle.isVisible()); assert(await toc.isHidden());
        assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
        assert(await page.locator('.mechanism-prose > section').first().evaluate(node =>
          node.getBoundingClientRect().top < innerHeight), 'article begins on the first screen');
        await toggle.focus(); await page.keyboard.press('Enter');
        assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
        assert(await toc.isVisible());
        await toggle.focus(); await page.keyboard.press('Space');
        assert(await toc.isHidden());
        await page.setViewportSize({width: 1440, height: 900});
        await toc.waitFor({state: 'visible'});
        assert(await toc.isVisible(), 'desktop navigation survives mobile collapse');
        await page.setViewportSize({width, height: 900});
        await toc.waitFor({state: 'hidden'});
        assert(await toc.isHidden());
      }
      if (width <= 960) { await toggle.focus(); await page.keyboard.press('Enter'); }
      await toc.locator('a[href="#faults"]').click();
      assert.equal(new URL(page.url()).hash, '#faults');
      if (width <= 960) { await toggle.click(); assert(await toc.isHidden()); }
      await page.evaluate(() => scrollTo(0, 0));
      await noOverflow(page);
      await page.screenshot({path: path.join(shots, `article-toc-${width}.png`)});

      await page.goto(`${base}/mechanisms/network-io.html?scenario=qemu-tx&step=publish`);
      const lab = page.locator('[data-execution-lab]');
      const align = page.locator('[data-execution-align]');
      const category = page.locator('[data-execution-category]');
      const note = page.locator('[data-execution-align-note]');
      await lab.locator('[data-execution-next]').waitFor();
      assert.equal(await lab.getAttribute('data-step'), 'publish');
      assert(!(await align.isChecked()), 'comparison is opt-in');
      await category.selectOption('vhost');
      assert.equal(await lab.getAttribute('data-step'), 'initial', 'default remains a fresh run');
      assert(await note.isHidden());
      await page.locator('[data-execution-phase="publish"]').click();
      await page.locator('[data-execution-next]').click(); // inside the phase, not its start
      await align.check();
      for (const backend of ['qemu', 'dpdk', 'vdpa', 'vhost']) {
        await category.selectOption(backend);
        assert.equal(await lab.getAttribute('data-scenario'), `${backend}-tx`);
        assert.equal(await lab.getAttribute('data-step'), 'publish', `${backend}: align the publication boundary`);
        assert(await note.isVisible());
        assert((await note.innerText()).includes('发布与'));
      }
      assert.equal(new URL(page.url()).searchParams.get('align'), 'phase');
      await page.reload();
      assert(await align.isChecked());
      assert.equal(await lab.getAttribute('data-step'), 'publish');
      await page.locator('[data-execution-operation="rx"]').click();
      assert.equal(await lab.getAttribute('data-step'), 'initial', 'TX/RX never share a position');
      assert((await note.innerText()).includes('没有可对齐'));
      await page.locator('[data-execution-next]').click();
      assert(await note.isHidden(), 'alignment notice expires when moving on');
      await align.uncheck();
      assert.equal(new URL(page.url()).searchParams.get('align'), null);
      await noOverflow(page);
      await page.locator('[data-execution-controls]').screenshot({path: path.join(shots, `network-compare-${width}.png`)});
    }
    assert.deepEqual(errors, []);
    await context.close();
    const plain = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 900}});
    await plain.route('**/*', localOnly);
    const plainPage = await plain.newPage();
    await plainPage.goto(`${base}/mechanisms/linux-memory.html`);
    assert(await plainPage.locator('#mechanism-toc-links').isVisible(), 'no-JS retains the article contents');
    assert(await plainPage.locator('[data-toc-toggle]').isHidden());
    await noOverflow(plainPage);
    await plain.close();
    console.log('PASS review improvements: mobile/desktop/no-JS TOC, keyboard access, backend phase alignment, direction reset and URL state');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
