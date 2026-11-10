/* Serial, local-only checks for the classic filter and compact paper rows. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const url = `${base}/conference-papers.html`;
const shots = process.env.PAPER_SCREENSHOT_DIR || '/tmp/virt-paper-browser';
const raw = JSON.parse(fs.readFileSync(path.join(__dirname, '../virt_report/content/conferences.json')));
const readings = JSON.parse(fs.readFileSync(path.join(__dirname, '../virt_report/content/paper_readings.json')));
const selected = raw.papers.filter(paper => Object.hasOwn(readings.papers, paper.id));
const selectedCount = selected.length;
const years = papers => [...new Set(papers.map(paper => paper.year))].sort((a, b) => b - a).map(String);
const visible = '[data-conference-item]:visible';
const classic = page => page.locator('[data-conference-classic]');
const options = async (page, key) => page.locator(`[data-conference-${key}] option`).evaluateAll(nodes =>
  nodes.filter(n => n.value).map(n => n.value));
async function ready(page) {
  await page.locator('[data-conference-enhanced]:visible').waitFor();
}
async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no horizontal overflow');
}
async function facetCounts(page, papers, key) {
  const actual = await page.locator(`[data-conference-${key}] option`).evaluateAll(nodes =>
    nodes.map(node => ({value: node.value, label: node.textContent})));
  for (const option of actual) {
    const count = option.value ? papers.filter(p => String(p[key]) === option.value).length : papers.length;
    assert(option.label.endsWith(`（${count}）`), `${key}/${option.value}: correct filtered count`);
    assert(!option.value || count > 0, 'unavailable options remain absent');
  }
}
(async () => {
  fs.mkdirSync(shots, {recursive: true});
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext();
    await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort());
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      await page.goto(url); await ready(page);
      assert.equal(await classic(page).count(), 1);
      assert.equal(await classic(page).getAttribute('role'), 'switch');
      assert.equal(await classic(page).getAttribute('aria-checked'), 'false', 'default is all papers');
      assert.deepEqual(await options(page, 'year'), years(raw.papers));
      await facetCounts(page, raw.papers, 'year');
      await facetCounts(page, raw.papers, 'venue');
      assert.equal(await page.locator(visible).first().getAttribute('data-year'), years(raw.papers)[0]);
      assert.equal(await classic(page).locator('.paper-switch-track').evaluate(node =>
        getComputedStyle(node, '::after').transform), 'none', 'default off-state knob');
      const gap = await page.evaluate(() => document.querySelector('.paper-search').getBoundingClientRect().top -
        document.querySelector('.conference-page > header > p').getBoundingClientRect().bottom);
      assert(gap >= 12 && gap <= 18, `compact intro/search spacing: ${gap}`);
      await page.screenshot({path: path.join(shots, `all-papers-${width}.png`)});
      await classic(page).click();
      assert.equal(await classic(page).getAttribute('aria-checked'), 'true');
      assert.equal(new URL(page.url()).searchParams.get('scope'), 'curated');
      await page.reload(); await ready(page);
      assert.equal(await classic(page).getAttribute('aria-checked'), 'true', 'explicit filter survives reload');
      assert.equal(await page.locator(visible).count(), Math.min(10, selectedCount));
      assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n => n.dataset.curated === 'true')));
      assert.equal(await page.locator(visible).first().getAttribute('data-year'), years(selected)[0]);
      assert.deepEqual(await options(page, 'year'), years(selected));
      assert.deepEqual((await options(page, 'venue')).sort(), [...new Set(selected.map(p => p.venue))].sort());
      assert.equal(await page.locator('.paper-reading, .paper-policy, .paper-resources, [data-conference-scope], [data-conference-count]').count(), 0);
      assert(await page.locator(visible).first().locator('h2 .paper-resource-tag').filter({hasText: /^PDF$/}).isVisible());
      await facetCounts(page, selected, 'year');
      await facetCounts(page, selected, 'venue');
      const switchBox = await classic(page).boundingBox();
      const barBox = await page.locator('.conference-controls').boundingBox();
      assert(switchBox.x > barBox.x + barBox.width / 2, 'classic switch is on the right');
      assert.equal(await classic(page).locator('.paper-switch-track').evaluate(node =>
        getComputedStyle(node, '::after').transform), 'matrix(1, 0, 0, 1, 16, 0)', 'on-state knob');
      await noOverflow(page);
      if (width <= 620) assert(await page.locator(visible).first().locator('.paper-meta')
        .evaluate(node => parseFloat(getComputedStyle(node).columnGap) >= 8), 'mobile metadata has breathing room');
      if (width <= 620) {
        const venueBox = await page.locator('[data-conference-venue]').boundingBox();
        const yearBox = await page.locator('[data-conference-year]').boundingBox();
        assert.equal(venueBox.x, yearBox.x, 'mobile select fields align');
        assert.equal(venueBox.width, yearBox.width);
      }
      await page.screenshot({path: path.join(shots, `classics-${width}.png`)});
      await page.locator('[data-conference-year]').selectOption(years(selected)[0]);
      const latest = selected.filter(p => String(p.year) === years(selected)[0]);
      assert.equal(await page.locator(visible).count(), latest.length);
      assert.deepEqual((await options(page, 'venue')).sort(), [...new Set(latest.map(p => p.venue))].sort());
      await facetCounts(page, latest, 'venue');
      await page.locator('[data-conference-reset]').click();
      await page.locator('[data-conference-next]').click();
      assert.equal(await page.locator(visible).count(), selectedCount - 10);
      assert(await page.locator('[data-conference-next]').isDisabled());
      await classic(page).click();
      assert.equal(await classic(page).getAttribute('aria-checked'), 'false');
      assert.equal(new URL(page.url()).searchParams.get('scope'), null, 'default scope has a clean URL');
      assert.deepEqual(await options(page, 'year'), years(raw.papers));
      assert.equal(await classic(page).locator('.paper-switch-track').evaluate(node =>
        getComputedStyle(node, '::after').transform), 'none', 'off-state knob');
      await facetCounts(page, raw.papers, 'year');
      const orders = await page.locator(visible).evaluateAll(nodes => nodes.map(n => Number(n.dataset.order)));
      assert.deepEqual(orders, Array.from({length: 10}, (_, i) => i));
      await page.locator('[data-conference-year]').selectOption('2026');
      assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n => n.dataset.year === '2026')));
      await classic(page).click();
      assert.equal(await page.locator('[data-conference-year]').inputValue(), '', 'unavailable year clears on scope change');
      assert.deepEqual(await options(page, 'year'), years(selected));
      assert.equal(new URL(page.url()).searchParams.get('year'), null);
      await page.goBack(); await ready(page);
      assert.equal(await classic(page).getAttribute('aria-checked'), 'false');
      assert.equal(await page.locator('[data-conference-year]').inputValue(), '2026', 'history restores options removed in classic scope');
      await page.locator('[data-conference-reset]').click();
      await page.locator('[data-conference-venue]').selectOption('asplos');
      await facetCounts(page, raw.papers.filter(p => p.venue === 'asplos'), 'year');
      await page.locator('[data-conference-year]').selectOption('2020');
      await page.locator('[data-conference-query]').fill('REAP');
      assert.equal(await page.locator(visible).count(), 1);
      assert.equal(await page.locator(visible).getAttribute('id'), 'paper-asplos21-reap');
      assert.deepEqual(await options(page, 'year'), ['2021']);
      assert.deepEqual(await options(page, 'venue'), ['asplos']);
      await facetCounts(page, raw.papers.filter(p => p.id === 'asplos21-reap'), 'year');
      assert.equal(await page.locator('[data-conference-year]').inputValue(), '');
      await classic(page).click();
      assert.equal(await page.locator(visible).count(), 0);
      assert(await page.locator('[data-conference-empty]').isVisible());
      assert.deepEqual(await options(page, 'year'), []);
      assert.deepEqual(await options(page, 'venue'), []);
      await classic(page).click();
      assert.equal(await page.locator(visible).count(), 1);
      await page.goBack(); await ready(page);
      assert.equal(await classic(page).getAttribute('aria-checked'), 'true');
      assert.equal(await page.locator(visible).count(), 0);
      await page.locator('[data-conference-reset]').click();
      assert.equal(await page.locator(visible).count(), 10);
      await noOverflow(page);
    }
    await page.goto(`${url}#paper-asplos21-reap`); await ready(page);
    assert.equal(await classic(page).getAttribute('aria-checked'), 'false');
    assert(await page.locator('#paper-asplos21-reap').isVisible(), 'ordinary search/deep links remain reachable');
    await page.reload(); await ready(page);
    assert(await page.locator('#paper-asplos21-reap').isVisible());
    await page.goto(`${url}?year=2026`); await ready(page);
    assert.equal(await classic(page).getAttribute('aria-checked'), 'false', 'legacy filters use all papers');
    assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n => n.dataset.year === '2026')));
    await page.goto(`${url}?scope=curated&year=2026`); await ready(page);
    assert.equal(await classic(page).getAttribute('aria-checked'), 'true');
    assert.equal(await page.locator('[data-conference-year]').inputValue(), '');
    assert.equal(await page.locator(visible).count(), 10, 'unavailable facet clears without widening scope');
    await page.goto(`${url}?scope=all&q=REAP`); await ready(page);
    assert.equal(await page.locator(visible).count(), 1);
    await page.locator(visible).locator('[data-paper-topic="Serverless"]').click();
    assert(new URL(page.url()).searchParams.get('topic') === 'Serverless');
    assert.equal(await classic(page).getAttribute('aria-checked'), 'false');
    await classic(page).focus(); await page.keyboard.press('Enter');
    assert.equal(await classic(page).getAttribute('aria-checked'), 'true', 'filter is keyboard operable');
    await page.keyboard.press('Space');
    assert.equal(await classic(page).getAttribute('aria-checked'), 'false', 'Space toggles the switch');
    assert.deepEqual(errors, []);
    await context.close();
    const plain = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 900}});
    await plain.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort());
    const plainPage = await plain.newPage();
    await plainPage.goto(url);
    assert.equal(await plainPage.locator(visible).count(), raw.papers.length, 'all papers readable without scripts');
    assert.equal(await plainPage.locator(visible).first().getAttribute('data-year'), years(raw.papers)[0]);
    await noOverflow(plainPage);
    await plainPage.screenshot({path: path.join(shots, 'papers-no-js.png')});
    await plain.close();
    console.log(`PASS paper filters: ${selectedCount} classics / ${raw.papers.length} total; nonempty facets, descending years, history, deep links, responsive, no-JS`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
