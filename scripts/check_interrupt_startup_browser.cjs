/* Local-only checks for irqfd, passthrough MSI-X routes and firmware/Guest boot. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = process.env.INTERRUPT_STARTUP_SCREENSHOTS || '/tmp/interrupt-startup-review';
fs.mkdirSync(shots, {recursive: true});

async function fits(page, tag) {
  const size = await page.evaluate(() => ({actual: document.documentElement.scrollWidth, width: innerWidth}));
  assert(size.actual <= size.width + 1, `${tag}: page overflow ${JSON.stringify(size)}`);
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [1440, 1024, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      await page.goto(`${base}/mechanisms/interrupt-delivery.html`);
      const menu = page.locator('[data-execution-scenario]');
      await menu.waitFor({state: 'visible'});
      assert.deepEqual(await menu.locator('optgroup').evaluateAll(nodes => nodes.map(n => n.label)), ['软件后端', '直通设备']);
      assert.deepEqual(await menu.locator('option').evaluateAll(nodes => nodes.map(n => n.value)),
        ['msix-software', 'irqfd', 'apicv-posted', 'vtd-remapped', 'vtd-posted']);
      const advanced = page.locator('[data-execution-advanced]');
      assert.equal(await advanced.evaluate(n => n.open), false);
      await advanced.locator('summary').click();
      await advanced.locator('[data-execution-advanced-scenario="msix-masked"]').click();
      assert.equal(await menu.inputValue(), 'msix-masked');
      assert.equal(await advanced.locator('[aria-pressed="true"]').textContent(), 'MSI-X 屏蔽与 PBA');
      await page.locator('[data-execution-next]').click();
      await page.reload();
      assert.equal(await advanced.evaluate(n => n.open), true, 'legacy scene URL opens its advanced entry');
      assert.equal(await menu.locator('option:checked').isDisabled(), true, 'advanced scene is not a primary preset');
      await menu.selectOption('irqfd');
      assert.equal(await advanced.evaluate(n => n.open), false);
      assert.equal(await page.locator('[data-execution-prev]').isDisabled(), true);
      for (const scenario of ['irqfd', 'apicv-posted']) {
        const step = scenario === 'irqfd' ? 'signal' : 'post';
        await page.goto(`${base}/mechanisms/interrupt-delivery.html?scenario=${scenario}&step=${step}`);
        await page.locator('[data-execution-controls]').waitFor({state: 'visible'});
        const state = await page.evaluate(() => {
          const lab = document.querySelector('[data-execution-lab]');
          return {frame: lab.executionSnapshot.frame,
            qemuNodes: lab.querySelectorAll('[data-component="qemu"] [data-execution-node]').length,
            backend: lab.querySelector('.walkthrough-domain-extension')?.textContent,
            hostNode: lab.querySelector('[data-component="host-kernel"] [data-execution-node]')?.dataset.executionNode};
        });
        assert.equal(state.frame.id, step);
        assert.equal(state.frame.values.used, '8');
        assert.equal(state.frame.values['last-used'], '7');
        assert.equal(state.qemuNodes, 0, 'QEMU is configuration, not an animated completion hop');
        assert(state.backend.includes('DPDK'));
        assert.equal(state.hostNode, 'eventfd');
        await fits(page, `${scenario}/${width}`);
        if ([1440, 390].includes(width)) {
          await page.locator('[data-execution-expand]').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'true');
          await fits(page, `${scenario}/${width}/fullscreen`);
          await page.screenshot({path: path.join(shots, `${scenario}-${width}.png`)});
          await page.locator('[data-execution-expand]').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'false');
        }
      }
      for (const [scenario, label] of [['vtd-remapped', 'MSI-X · 宿主转发'],
        ['vtd-posted', 'MSI-X · VT-d posted']]) {
        await page.goto(`${base}/mechanisms/interrupt-delivery.html?scenario=${scenario}&step=emit`);
        await page.locator('[data-execution-controls]').waitFor({state: 'visible'});
        assert.equal(await menu.locator('option:checked').textContent(), label);
        const frame = await page.locator('[data-execution-lab]').evaluate(n => n.executionSnapshot.frame);
        assert.equal(frame.id, 'emit');
        assert.equal(frame.values.message, '已发送 · index 0x20 / subhandle 0');
        assert.equal(frame.values['guest-vector'], '0x51');
        assert((await page.locator('[data-execution-summary]').textContent()).includes('MSI-X'));
        await fits(page, `${scenario}/${width}`);
        if ([1440, 390].includes(width)) {
          await page.locator('[data-execution-expand]').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'true');
          await fits(page, `${scenario}/${width}/fullscreen`);
          await page.screenshot({path: path.join(shots, `${scenario}-${width}.png`)});
          await page.locator('[data-execution-expand]').click();
          await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'false');
        }
      }
      await page.goto(`${base}/mechanisms/interrupt-virtualization.html`);
      const acceleration = page.locator('img[src*="interrupt-acceleration-layers.svg"]');
      // Earlier lazy diagrams can shift this image out of view during loading.
      await acceleration.evaluate(node => { node.loading = 'eager'; });
      await page.waitForFunction(node => node.complete && node.naturalWidth > 0,
        await acceleration.elementHandle());
      await acceleration.scrollIntoViewIfNeeded();
      await acceleration.evaluate(node => node.decode());
      assert(await acceleration.evaluate(n => n.naturalWidth > 0 && n.width <= n.parentElement.clientWidth + 1));
      await fits(page, `interrupt-acceleration/${width}`);
      if ([1440, 390].includes(width)) {
        await page.screenshot({path: path.join(shots, `interrupt-acceleration-${width}.png`)});
      }
      await page.goto(`${base}/mechanisms/qemu-vm-startup.html`);
      await fits(page, `startup/${width}`);
      // Settle lazy-loaded diagrams before capturing sections below them.
      for (const image of await page.locator('img[src*="qemu-startup-"]').all()) {
        await image.scrollIntoViewIfNeeded();
        await image.evaluate(node => node.decode());
      }
      for (const id of ['firmware', 'first-run', 'guest-boot']) {
        const section = page.locator(`#${id}`);
        assert(await section.isVisible());
        assert.equal(await page.locator(`.mechanism-toc a[href="#${id}"]`).count(), 1);
        if ([1440, 390].includes(width)) {
          await section.evaluate(node => node.scrollIntoView({block: 'start', behavior: 'instant'}));
          await page.screenshot({path: path.join(shots, `startup-${id}-${width}.png`), animations: 'disabled'});
        }
      }
      const images = await page.locator('img[src*="qemu-startup-"]').evaluateAll(nodes =>
        nodes.map(n => ({loaded: n.complete && n.naturalWidth > 0, width: n.width, client: n.parentElement.clientWidth})));
      assert.equal(images.length, 2);
      assert(images.every(n => n.loaded && n.width <= n.client + 1), JSON.stringify(images));
      console.log(`PASS irqfd, MSI-X delivery routes, advanced entry, diagrams, URL restore and firmware-to-userspace: ${width}px`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
