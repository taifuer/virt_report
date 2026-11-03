/* Local-only: one completion notification, independent buffer lifetimes and quiet CPU lanes. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = '/tmp/completion-boundaries-review';
fs.mkdirSync(shots, {recursive: true});

async function open(page, slug, scenario, step) {
  await page.goto(`${base}/mechanisms/${slug}.html?${new URLSearchParams({scenario, step})}`);
  await page.locator('[data-execution-next]').waitFor();
}
async function state(page) {
  return page.locator('[data-execution-lab]').evaluate(lab => ({
    step: lab.dataset.step,
    fields: Object.fromEntries([...lab.querySelectorAll('[data-completion-field]')]
      .map(n => [n.dataset.completionField, n.querySelector('dd').textContent])),
    nodes: lab.executionSnapshot.frame.nodes,
    queue: lab.executionSnapshot.frame.queueState,
  }));
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: width > 700 ? 1000 : 844});
      await open(page, 'network-io', 'dpdk-tx', 'used');
      let actual = await state(page);
      assert.equal(actual.fields.applicationBoundary, '返回 18 · 不等待设备完成');
      assert.equal(actual.fields.queueBoundary, 'used 已发布 · 可回收');
      assert.equal(actual.fields.interruptBoundary, '尚未发起');
      assert.equal(actual.fields.transportBoundary, '独立副本 · 尚未提交 PMD');
      for (const step of ['notify', 'route-interrupt', 'pmd', 'prepare-interrupt', 'guest-interrupt']) {
        await page.locator('[data-execution-next]').click();
        actual = await state(page); assert.equal(actual.step, step);
        if (step === 'pmd') {
          assert.equal(actual.fields.interruptBoundary, 'vLAPIC 待处理 · 尚未注入');
          assert.equal(actual.fields.transportBoundary, 'PMD 已接受 · 等待网卡完成');
          await page.locator('[data-execution-controls]').screenshot({path: `${shots}/dpdk-tx-${width}.png`});
        }
      }
      await page.locator('[data-execution-reset]').click();
      assert.equal((await state(page)).fields.interruptBoundary, '尚未发起');
      // A phase jump, one click and one playback tick must show the same event.
      const phase = page.locator('[data-execution-phase="fill-used"]');
      await phase.click(); await page.locator('[data-execution-next]').click();
      const expected = await state(page);
      await phase.click(); await page.locator('[data-execution-speed]').selectOption('1200');
      await page.locator('[data-execution-play]').click();
      await page.waitForFunction(() => document.querySelector('[data-execution-lab]').dataset.step === 'used');
      await page.locator('[data-execution-play]').click();
      assert.deepEqual(await state(page), expected);

      for (const backend of ['qemu', 'vhost', 'dpdk', 'vdpa']) {
        await open(page, 'network-io', `${backend}-rx`, 'socket-enqueue');
        actual = await state(page);
        assert.equal(actual.fields.applicationBoundary, 'socket 已有数据 · 尚未读取');
        assert(actual.nodes.application.includes('尚未调用 recvfrom'));
        assert.equal(actual.queue.used.consumer, 1);
        await page.locator('[data-execution-next]').click();
        actual = await state(page);
        assert.equal(actual.step, 'recvfrom-wait'); // Existing deep-link ID retained.
        assert.equal(actual.fields.applicationBoundary, '读取已排队的数据报');
        assert.equal(actual.queue.buffers[0].state, 'socket');
        await page.locator('[data-execution-prev]').click();
        assert.equal((await state(page)).fields.applicationBoundary, 'socket 已有数据 · 尚未读取');
      }
      await open(page, 'cpu-execution', 'mmio-userspace', 'resume');
      // The exact final step ID is intentionally resolved via the controls.
      await page.locator('[data-execution-lab]').evaluate(lab => {
        const next = lab.querySelector('[data-execution-next]');
        for (let n = 0; !next.disabled && n < 100; n++) next.click();
      });
      assert.equal(await page.locator('[data-cpu-lane][aria-current="location"]').count(), 1);
      assert.equal(await page.locator('[data-cpu-lane][data-current="false"] .execution-node-status').count(), 0);
      assert(!(await page.locator('[data-execution-timeline]').textContent()).includes('本步未在此执行'));
      await page.locator('[data-execution-controls]').screenshot({path: `${shots}/cpu-mmio-${width}.png`});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      console.log(`PASS ${width}px: completion boundaries, RX before recvfrom, phase/playback equivalence, CPU lanes`);
    }
    assert.deepEqual(errors, []);
    console.log(`Screenshots: ${shots}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
