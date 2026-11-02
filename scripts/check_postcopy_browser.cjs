/* Local-only regression: page delivery and resuming the faulting access are separate. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const model = require('../virt_report/render/assets/postcopy.js');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const origin = new URL(base).origin;
assert(['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname), 'use a local preview');

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname.endsWith('/postcopy.js')) return route.fulfill({contentType: 'application/javascript',
      body: fs.readFileSync(path.resolve(__dirname, '../virt_report/render/assets/postcopy.js'), 'utf8')});
    return route.continue();
  });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let checks = 0;
  try {
    for (const width of [1366, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(`${base}/mechanisms/qemu-postcopy.html`);
      const lab = page.locator('[data-postcopy-lab]');
      await lab.locator('[data-postcopy-controls]').waitFor({state: 'visible'});
      let state = model.createState();
      async function step(kind = 'advance', number = 2) {
        if (['access', 'write'].includes(kind)) await lab.locator('[data-postcopy-page]').selectOption(String(number));
        await lab.locator(`[data-postcopy-${kind === 'advance' ? 'next' : kind}]`).click();
        state = model.apply(state, {kind, page: number});
        const actual = await lab.evaluate(node => ({
          cpu: node.dataset.cpuState, blocked: node.dataset.blocked, rejected: node.dataset.rejected,
          status: node.querySelector('[data-postcopy-status]').textContent,
          pages: [...node.querySelectorAll('[data-postcopy-destination] .postcopy-page strong')].map(n => n.textContent),
          next: node.querySelector('[data-postcopy-next]').textContent,
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
        }));
        assert.equal(actual.cpu, !model.ready(state) ? 'stopped' : state.waiting ? 'waiting' : state.retry ? 'ready-to-retry' : 'running');
        assert.equal(actual.blocked, String(Boolean(state.waiting)));
        assert.equal(actual.rejected, String(state.rejected));
        assert.equal(actual.status, state.status);
        assert.deepEqual(actual.pages, state.destination.map(value => value === null ? '缺失' : `v${value}`));
        if (state.retry) assert.match(actual.next, /调度与重试/);
        assert(!actual.overflow, `${width}: page overflow`);
        checks++;
      }
      // Install a faulting write, then send another page before the vCPU runs.
      for (let n = 0; n < 3; n++) await step();
      await step('write', 2);
      for (let n = 0; n < 5; n++) await step();
      assert(state.retry); assert.equal(state.destination[2], 2);
      await step('write', 0); assert(state.rejected);
      await step('background'); assert(state.retry); assert(state.packet);
      assert.equal(state.destination[2], 2);
      await step(); assert.equal(state.retry, null); assert.equal(state.destination[2], 3);
      // Reset and leave the last page for a faulting write.
      await lab.locator('[data-postcopy-reset]').click(); state = model.createState();
      for (let n = 0; n < 3; n++) await step();
      for (let page = 0; page < 4; page++) {
        await step('background'); await step(); await step();
      }
      await step('write', 7);
      for (let n = 0; n < 5; n++) await step();
      assert(model.complete(state)); assert(state.retry);
      assert.equal(state.destination[7], 1);
      await step(); assert.equal(state.destination[7], 2); assert.equal(state.retry, null);
      // The guided example retains the same boundary and reaches its final state.
      await lab.locator('[data-postcopy-reset]').click(); state = model.createState();
      for (const action of model.example) await step(action.kind, action.page ?? 2);
      assert(model.complete(state)); assert.equal(state.retry, null); assert.equal(state.destination[2], 3);
      console.log(`PASS postcopy/${width}: install, wake, retry, independent background transfer and final-page boundary`);
    }
    assert.deepEqual(errors, []);
    console.log(`PASS ${checks} postcopy browser state checks; external requests blocked`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
