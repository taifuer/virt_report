/* Local-only regression for category-scoped presets and CPU BAR address mappings. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = process.env.MEMORY_MAPPING_SCREENSHOTS || '/tmp/memory-mapping-review';
fs.mkdirSync(shots, {recursive: true});
const route = `${base}/mechanisms/memory-access.html`;
const control = (page, name) => page.locator(`[data-execution-${name}]`);
const snapshot = page => control(page, 'lab').evaluate(node => ({
  id: node.executionSnapshot.frame.id,
  values: node.executionSnapshot.frame.values,
  summary: Object.fromEntries([...node.querySelectorAll('[data-completion-field]')]
    .map(cell => [cell.dataset.completionField, cell.querySelector('dd').textContent])),
}));

async function fits(page, label) {
  const size = await page.evaluate(() => ({width: innerWidth, actual: document.documentElement.scrollWidth}));
  assert(size.actual <= size.width + 1, `${label}: page overflow ${JSON.stringify(size)}`);
}

async function load(page, scene, step = 'ready') {
  await page.goto(`${route}?scenario=${scene}&step=${step}`);
  await control(page, 'controls').waitFor({state: 'visible'});
  assert.equal(await control(page, 'scenario').inputValue(), scene);
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', request => request.request().url().startsWith(base + '/')
    ? request.continue() : request.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      await load(page, 'ram-hit');
      const advanced = control(page, 'advanced');
      assert(await advanced.isVisible());
      assert.equal(await advanced.evaluate(node => node.open), false);
      assert.deepEqual(await control(page, 'scenario').locator('option:not(:disabled)').evaluateAll(nodes => nodes.map(node => node.value)),
        ['ram-hit', 'tlb-miss', 'guest-page-fault', 'ept-missing']);
      await advanced.locator('summary').click();
      await page.locator('[data-execution-advanced-scenario="host-page-fault"]').click();
      assert.equal(await control(page, 'scenario').inputValue(), 'host-page-fault');
      await control(page, 'next').click();
      const fault = await snapshot(page);
      await page.reload();
      await control(page, 'controls').waitFor({state: 'visible'});
      assert.deepEqual(await snapshot(page), fault, 'old advanced URLs restore progress');
      assert.equal(await advanced.evaluate(node => node.open), true);
      await control(page, 'category').selectOption('mmio');
      assert(await advanced.isHidden(), 'RAM advanced controls must not leak into device category');
      assert.equal(await advanced.evaluate(node => node.open), false);
      assert.equal(await control(page, 'scenario').inputValue(), 'mmio-read');
      await control(page, 'category').selectOption('ram');
      assert.equal(await control(page, 'scenario').inputValue(), 'ram-hit');
      assert.equal(await advanced.evaluate(node => node.open), false);
      await load(page, 'dirty-write-protect', 'mark');
      assert.equal(await control(page, 'category').inputValue(), 'ram');
      assert.equal(await advanced.evaluate(node => node.open), true);
      assert.equal((await snapshot(page)).id, 'mark');
      await control(page, 'scenario').selectOption('ept-missing');
      assert.equal(await advanced.evaluate(node => node.open), false);
      await fits(page, `RAM presets/${width}`);

      for (const [scene, hpa, target] of [
        ['mmio-vfio-direct', '0xd0001000', '物理设备 MMIO'],
        ['mmio-ivshmem-direct', '0x21001000', '宿主共享 RAM'],
      ]) {
        await load(page, scene, 'access');
        assert.equal(await control(page, 'category').inputValue(), 'mmio');
        assert(await advanced.isHidden());
        const expected = await snapshot(page);
        assert.deepEqual(expected.summary, {gpa: '0xf0001000', hpa, offset: '0x1000', target});
        for (const [key, value] of Object.entries(expected.summary)) assert.equal(value, expected.values[key]);
        for (const component of ['kvm', 'qemu', 'host-kernel']) {
          assert.equal(await page.locator(`[data-component="${component}"] [data-execution-node]`).count(), 0,
            `${scene}/${component}: configuration notes are not fabricated runtime actors`);
        }
        if (scene === 'mmio-ivshmem-direct') {
          assert.equal(await page.locator('.walkthrough-memory [data-execution-node="ram"]').count(), 1);
          assert.equal(await page.locator('[data-component="hardware"] [data-execution-node="ram"]').count(), 0);
        }
        for (const view of ['overview', 'structure', 'source']) {
          await page.locator(`[data-execution-view="${view}"]`).click();
          assert.deepEqual(await snapshot(page), expected, `${scene}/${view}: view changes preserve state`);
          await fits(page, `${scene}/${view}/${width}`);
        }
        await page.reload();
        await control(page, 'controls').waitFor({state: 'visible'});
        assert.deepEqual(await snapshot(page), expected, 'deep link restores the same mapping');
        await page.locator('[data-execution-view="overview"]').click();
        if ([1440, 390].includes(width)) {
          await control(page, 'expand').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'true');
          await fits(page, `${scene}/fullscreen/${width}`);
          await page.screenshot({path: path.join(shots, `${scene}-${width}.png`)});
          await control(page, 'expand').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'false');
          assert.deepEqual(await snapshot(page), expected, 'fullscreen preserves state');
        }
      }
      console.log(`PASS RAM advanced category, BAR mapping comparison, three views and deep links: ${width}px`);
    }

    await page.setViewportSize({width: 1440, height: 1000});
    for (const scene of ['mmio-vfio-direct', 'mmio-ivshmem-direct']) {
      await load(page, scene);
      const manual = [await snapshot(page)];
      while (!await control(page, 'next').isDisabled()) {
        await control(page, 'next').click();
        manual.push(await snapshot(page));
      }
      assert.deepEqual(manual.map(frame => frame.id), ['ready', 'translate', 'access', 'return', 'complete']);
      await control(page, 'reset').click();
      assert.deepEqual(await snapshot(page), manual[0]);
      await page.evaluate(() => {
        const lab = document.querySelector('[data-execution-lab]');
        lab.recorded = [];
        lab.addEventListener('execution:frame', event => {
          lab.recorded.push({id: event.detail.frame.id, values: event.detail.frame.values,
            summary: Object.fromEntries([...lab.querySelectorAll('[data-completion-field]')]
              .map(cell => [cell.dataset.completionField, cell.querySelector('dd').textContent]))});
        });
      });
      await control(page, 'speed').selectOption('1200');
      await control(page, 'play').click();
      await page.waitForFunction(() => document.querySelector('[data-execution-next]').disabled
        && document.querySelector('[data-execution-play]').getAttribute('aria-pressed') === 'false');
      assert.deepEqual(await control(page, 'lab').evaluate(node => node.recorded), manual.slice(1),
        `${scene}: playback and next use identical snapshots`);
      await control(page, 'reset').click();
      assert.deepEqual(await snapshot(page), manual[0]);
    }
    assert.deepEqual(errors, []);
    console.log('PASS BAR manual/playback equivalence, reset, fullscreen and no console errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
