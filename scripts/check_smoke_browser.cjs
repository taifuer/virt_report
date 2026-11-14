/* Essential user journeys; exhaustive states belong to models and opt-in audits. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'use a local preview only');
const artifacts = process.env.BROWSER_ARTIFACT_DIR || '/tmp/virt-report-browser/smoke';
const catalogue = require('../virt_report/content/conferences.json');
const reviews = catalogue.papers.filter(p => ['survey', 'monograph'].includes(p.publication_type));

async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    `horizontal overflow: ${page.url()}`);
}

async function papers(page) {
  const classic = page.locator('[data-conference-filter="curated"]');
  const survey = page.locator('[data-conference-filter="surveys"]');
  await survey.waitFor({state: 'visible'});
  const before = await survey.boundingBox();
  await survey.click();
  assert.equal(await survey.getAttribute('aria-pressed'), 'true');
  const pageSize = Number(await page.locator('[data-conference-browser]').getAttribute('data-default-size'));
  assert(reviews.length > 0 && pageSize > 0);
  assert.equal(await page.locator('[data-conference-item]:visible').count(), Math.min(pageSize, reviews.length));
  assert.equal(new URL(page.url()).searchParams.get('scope'), 'surveys');
  const after = await survey.boundingBox();
  for (const key of ['x', 'y', 'width', 'height']) assert(Math.abs(before[key] - after[key]) <= 1, key);
  await page.reload();
  assert.equal(await survey.getAttribute('aria-pressed'), 'true');
  await classic.click();
  assert.equal(await survey.getAttribute('aria-pressed'), 'false');
  assert.equal(await classic.getAttribute('aria-pressed'), 'true');
  const visible = page.locator('[data-conference-item]:visible');
  assert(await visible.count() > 0);
  assert(await visible.evaluateAll(nodes => nodes.every(n => n.dataset.curated === 'true')));
  await page.goBack();
  assert.equal(await survey.getAttribute('aria-pressed'), 'true');
  await survey.click();
  assert.equal(new URL(page.url()).searchParams.get('scope'), null);
  await page.locator('[data-conference-next]').click();
  assert.equal(new URL(page.url()).searchParams.get('page'), '2');
}

async function versions(page) {
  await page.locator('[data-version-project]').selectOption('virtio');
  assert(await page.locator('label[for="version-topic"]').isHidden());
  const years = await page.locator('[data-version-item][data-project="virtio"]').evaluateAll(nodes =>
    [...new Set(nodes.map(n => n.dataset.year))].sort().reverse());
  assert(years.length > 0);
  assert.deepEqual(await page.locator('[data-version-year] option').evaluateAll(nodes => nodes.map(n => n.value)),
    ['', ...years]);
  await page.locator('[data-version-year]').selectOption(years[0]);
  await page.reload();
  assert.equal(await page.locator('[data-version-project]').inputValue(), 'virtio');
  const rows = await page.locator('[data-version-item]:visible').evaluateAll(nodes => nodes.map(n => ({...n.dataset})));
  assert(rows.length > 0 && rows.every(n => n.project === 'virtio' && n.year === years[0]));
  await page.locator('[data-version-project]').selectOption('qemu');
  assert(await page.locator('label[for="version-topic"]').isVisible());
}

async function search(page) {
  await page.locator('#search-query').fill('Dune');
  await page.locator('#search-query').press('Enter');
  await page.locator('[data-editorial-kind="papers"] a[href="conference-papers.html#paper-osdi12-dune"]').click();
  await page.locator('#paper-osdi12-dune:visible').waitFor();
  await page.goBack();
  assert.equal(await page.locator('#search-query').inputValue(), 'Dune');
  await page.locator('[data-search-clear]').click();
  await page.waitForFunction(() => document.querySelector('[data-editorial-results]').hidden);
}

async function article(page, width) {
  const toc = page.locator('#mechanism-toc-links');
  const toggle = page.locator('[data-toc-toggle]');
  assert.equal(await page.locator('.mechanism-chapters,.mechanism-adjacent,.mechanism-page-outline').count(), 0);
  assert(await toc.locator('a').evaluateAll(links => links.every(a => a.getAttribute('href').startsWith('#'))));
  if (width <= 960) {
    assert(await toc.isHidden());
    await toggle.focus();
    await page.keyboard.press('Enter');
    assert(await toc.isVisible());
    await toggle.click();
    assert(await toc.isHidden());
  } else {
    assert(await toc.isVisible());
    const contentBox = await page.locator('.mechanism-prose').boundingBox();
    const tocBox = await page.locator('.mechanism-toc').boundingBox();
    assert(tocBox.x >= contentBox.x + contentBox.width, 'article contents stay on the right');
    assert(await toc.locator('a').evaluateAll(links => links.every(a =>
      getComputedStyle(a).borderLeftWidth === '0px' && getComputedStyle(a).boxShadow === 'none')),
    'no decorative color bars on contents links');
  }
  const expand = page.locator('.mechanism-diagram [data-mechanism-expand]').first();
  await expand.click();
  const dialog = page.locator('.mechanism-viewer[open]');
  await dialog.waitFor();
  await page.keyboard.press('Escape');
  await dialog.waitFor({state: 'detached'});
  assert(await expand.evaluate(node => node === document.activeElement));
}

async function network(page) {
  const lab = page.locator('[data-execution-lab]');
  await lab.scrollIntoViewIfNeeded();
  await page.locator('[data-execution-phase="publish"]').click();
  assert.equal(await lab.getAttribute('data-step'), 'publish');
  await page.locator('[data-execution-next]').click();
  assert.notEqual(await lab.getAttribute('data-step'), 'publish');
  await page.locator('[data-execution-align]').check();
  await page.locator('[data-execution-category]').selectOption('vhost');
  assert.equal(await lab.getAttribute('data-scenario'), 'vhost-tx');
  assert.equal(await lab.getAttribute('data-step'), 'publish');
  await page.locator('[data-execution-view="structure"]').click();
  assert.equal(await lab.getAttribute('data-view'), 'structure');
  await page.locator('[data-execution-reset]').click();
  assert.equal(await lab.getAttribute('data-step'), 'initial');
}

async function disk(page) {
  const lab = page.locator('[data-vbr-lab]');
  await lab.scrollIntoViewIfNeeded();
  const next = page.locator('[data-vbr-next]');
  await next.waitFor({state: 'visible'});
  const start = await lab.getAttribute('data-step');
  await next.click();
  assert.notEqual(await lab.getAttribute('data-step'), start);
  await page.locator('[data-vbr-reset]').click();
  assert.equal(await lab.getAttribute('data-step'), start);
  await page.locator('[data-vbr-view="mechanism"]').click();
  assert(await page.locator('[data-vbr-structure]').isVisible());
}

async function queue(page) {
  const lab = page.locator('[data-packed-lab]');
  const action = name => lab.locator(`[data-packed-action="${name}"]`);
  await action('publish').click();
  assert.match(await lab.locator('[data-packed-status]').textContent(), /avail.idx=1/);
  await lab.locator('[data-queue-format="packed"]').click();
  assert.match(await lab.locator('[data-packed-status]').textContent(), /A\/U=1\/0/);
  await action('complete').click();
  assert(await action('reclaim').isEnabled());
  await action('reclaim').click();
  await action('reset').click();
  assert.equal(await lab.locator('.is-available,.is-completed').count(), 0);
}

function assertReferenceEntries(entries) {
  assert(entries.length > 0, 'the reference section must not be empty');
  for (const links of entries) {
    assert(links.length > 0, 'each reference entry must include a source link');
    for (const link of links) {
      assert(link.visible, 'source links must be visible without expansion');
      assert(link.text.trim(), 'source links must have readable labels');
      assert.match(new URL(link.href).protocol, /^https?:$/, 'source links must use absolute web URLs');
    }
  }
}

async function categories(page) {
  assert.equal(await page.locator('h1').innerText(), '原理');
  assert.equal(await page.locator('a[href="kvm/"],.mechanism-book-chapter').count(), 0);
  await page.locator('.mechanism-directory a[href="#kvm-internals"]').click();
  await page.locator('#kvm-internals h3 a[href="mechanisms/kvm-vcpu-run.html"]').click();
  assert.equal(await page.locator('.mechanism-toc').count(), 1);
  assert.equal(await page.locator('.mechanism-chapters,a[rel="next"]').count(), 0);
  await page.goto(`${base}/mechanisms/virtualization-basics.html`);
  assert.match(await page.locator('.mechanism-breadcrumb').innerText(), /虚拟化基础/);
  assert.doesNotMatch(await page.locator('.mechanism-breadcrumb').innerText(), /KVM/);
  assert.equal(await page.locator('#sources details').count(), 0);
  assert(await page.locator('#sources').isVisible(), 'the reference section stays visible');
  // Editorial reference counts can change; smoke checks cover their presentation.
  const references = await page.locator('#sources li').evaluateAll(items => items.map(item =>
    [...item.querySelectorAll('a')].map(link => ({
      text: link.textContent, href: link.getAttribute('href'),
      visible: link.getClientRects().length > 0 && getComputedStyle(link).visibility === 'visible',
    }))));
  assertReferenceEntries(references);
  await page.goto(`${base}/kvm/`);
  await page.waitForURL(`${base}/mechanisms.html#kvm-internals`);
  assert.equal(await page.locator('h1').innerText(), '原理');
  assert(await page.locator('#kvm-internals .mechanism-list').isVisible());
}

async function run() {
  fs.mkdirSync(artifacts, {recursive: true});
  for (const name of ['failure.png', 'failure-trace.zip', 'failure.json']) {
    fs.rmSync(path.join(artifacts, name), {force: true});
  }
  const browser = await launchBrowser({headless: true});
  console.log(`Browser: ${browser.version()} (${process.env.PLAYWRIGHT_CHANNEL || 'bundled chromium'})`);
  const context = await browser.newContext({reducedMotion: 'reduce'});
  context.setDefaultTimeout(8000);
  context.setDefaultNavigationTimeout(15000);
  await context.route('**/*', route => new URL(route.request().url()).origin === base
    ? route.continue() : route.abort());
  await context.tracing.start({screenshots: true, snapshots: true});
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  let checking = 'startup';
  try {
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      for (const [url, check] of [
        ['/conference-papers.html', papers], ['/versions.html', versions], ['/search.html', search],
        ['/mechanisms/linux-memory.html', article], ['/mechanisms/network-io.html', network],
        ['/mechanisms/qemu-virtio-blk.html', disk], ['/mechanisms/packed-virtqueue.html', queue],
        ['/mechanisms.html', categories],
      ]) {
        checking = `${url} at ${width}px`;
        const started = performance.now();
        assert.equal((await page.goto(base + url)).status(), 200);
        await check(page, width);
        await noOverflow(page);
        assert.deepEqual(errors, []);
        console.log(`PASS ${checking} (${((performance.now() - started) / 1000).toFixed(1)}s)`);
      }
    }
    checking = 'no-JS article';
    const plain = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 900}});
    await plain.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    const reader = await plain.newPage();
    await reader.goto(`${base}/mechanisms/linux-memory.html`);
    assert(await reader.locator('#mechanism-toc-links').isVisible());
    assert(await reader.locator('[data-toc-toggle]').isHidden());
    await noOverflow(reader);
    await plain.close();
    await context.tracing.stop();
    console.log('PASS eight essential journeys, desktop/mobile, keyboard, URL state and no-JS; no success screenshots');
  } catch (error) {
    console.error(`FAIL ${checking}`);
    await page.screenshot({path: path.join(artifacts, 'failure.png'), timeout: 5000}).catch(() => {});
    await context.tracing.stop({path: path.join(artifacts, 'failure-trace.zip')}).catch(() => {});
    fs.writeFileSync(path.join(artifacts, 'failure.json'), JSON.stringify({checking, errors, error: String(error)}, null, 2));
    throw error;
  } finally { await browser.close(); }
}
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = {assertReferenceEntries};
