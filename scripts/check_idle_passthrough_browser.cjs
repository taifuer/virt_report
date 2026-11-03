/* Local-only regression for HLT interception versus fixed exitless posted wakeup. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = process.env.IDLE_PASSTHROUGH_SCREENSHOTS || '/tmp/idle-passthrough-review';
fs.mkdirSync(shots, {recursive: true});
const route = `${base}/mechanisms/cpu-execution.html`;
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
      for (const scene of ['hlt-wakeup', 'hlt-passthrough']) {
        const step = scene === 'hlt-wakeup' ? 'sleep' : 'halt';
        await page.goto(`${route}?scenario=${scene}&step=${step}`);
        await control(page, 'controls').waitFor({state: 'visible'});
        assert.equal(await control(page, 'scenario').inputValue(), scene);
        const expected = await snapshot(page);
        assert.equal(expected.id, step);
        assert.deepEqual(Object.keys(expected.summary), ['mode', 'exit-count', 'task-state', 'run-state']);
        for (const [key, value] of Object.entries(expected.summary)) assert.equal(value, expected.values[key]);
        assert.equal(expected.summary['exit-count'], scene === 'hlt-wakeup' ? '1（HLT）' : '0');
        assert.equal(expected.summary['run-state'], '调用中，未返回');
        if (scene === 'hlt-passthrough') {
          assert.equal(expected.values.activity, 'HLT（硬件等待）');
          assert.equal(expected.values['mp-state'], 'RUNNABLE');
          assert.equal(expected.values['task-state'], 'TASK_RUNNING，未阻塞');
          assert.equal(await page.locator('[data-component="kvm"] [data-execution-node]').getAttribute('data-execution-node'), 'sender');
          assert.equal(await page.locator('[data-component="qemu"] [data-execution-node]').count(), 0);
          assert((await page.locator('.walkthrough-memory').textContent()).includes('宿主内存 · PID'));
        }
        for (const view of ['overview', 'structure', 'source']) {
          await page.locator(`[data-execution-view="${view}"]`).click();
          assert.deepEqual(await snapshot(page), expected, `${scene}/${view}: view changes preserve state`);
          await fits(page, `${scene}/${view}/${width}`);
        }
        await page.reload();
        await control(page, 'controls').waitFor({state: 'visible'});
        assert.deepEqual(await snapshot(page), expected, 'deep link restores the same step');
        await page.locator('[data-execution-view="overview"]').click();
        if ([1440, 390].includes(width)) {
          await control(page, 'expand').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'true');
          await fits(page, `${scene}/fullscreen/${width}`);
          await page.screenshot({path: path.join(shots, `${scene}-${width}.png`)});
          await control(page, 'expand').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'false');
          assert.deepEqual(await snapshot(page), expected, 'fullscreen does not advance the trace');
        }
      }
      console.log(`PASS HLT comparison, state summary, three views and deep links: ${width}px`);
    }
    await page.setViewportSize({width: 1440, height: 1000});
    for (const scene of ['hlt-wakeup', 'hlt-passthrough']) {
      await page.goto(`${route}?scenario=${scene}`);
      await control(page, 'controls').waitFor({state: 'visible'});
      const manual = [await snapshot(page)];
      while (!await control(page, 'next').isDisabled()) {
        await control(page, 'next').click();
        manual.push(await snapshot(page));
      }
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
      const played = await control(page, 'lab').evaluate(node => node.recorded);
      assert.deepEqual(played, manual.slice(1), `${scene}: playback and next use identical snapshots`);
      await control(page, 'reset').click();
      assert.deepEqual(await snapshot(page), manual[0]);
    }
    assert.deepEqual(errors, []);
    console.log('PASS manual/playback equivalence, reset, fullscreen and no console errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
