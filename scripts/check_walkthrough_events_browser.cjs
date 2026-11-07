/* Shared event views, state inspection and balloon accounting in a real browser. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8093').replace(/\/$/, '');
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const shots = '/tmp/virt-report-event-review'; fs.mkdirSync(shots, {recursive:true});
const pages = ['network-io','virtio-blk-read','cpu-execution','memory-access',
  'interrupt-delivery','gpu-execution','virtio-gpu-execution','libvirt-domain-start','virtio-balloon'];
(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      for (const slug of pages) {
        await page.goto(`${base}/mechanisms/${slug}.html`, {waitUntil:'networkidle'});
        const prefix = slug === 'virtio-blk-read' ? 'vbr' : 'execution';
        const lab = page.locator(`[data-${prefix}-lab]`);
        await page.locator(`[data-${prefix}-view="sequence"]`).click();
        const events = lab.locator('[data-event-id]');
        assert(await events.count() > 3, `${slug} has events`);
        await events.nth(3).click();
        const step = await lab.getAttribute('data-step');
        assert.equal(await lab.locator('[data-event-id][aria-current="step"]').getAttribute('data-event-id'), step);
        assert.equal(await lab.locator('svg path[d*="NaN"]').count(), 0);
        await page.locator(`[data-${prefix}-view="overview"]`).click();
        assert.equal(await lab.getAttribute('data-step'), step);
        await page.locator(`[data-${prefix}-next]`).click();
        assert.notEqual(await lab.getAttribute('data-step'), step);
        await lab.locator('.walkthrough-inspection summary').click();
        assert(await lab.locator('.walkthrough-inspection').evaluate(node => node.open));
        if (prefix === 'execution') {
          const objects = lab.locator('button.execution-node:visible');
          await objects.first().click();
          assert(await lab.locator('[data-execution-inspection] h4').isVisible(), `${slug} object inspection`);
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        assert(!overflow, `${slug} page overflow at ${width}`);
        const clipped = await lab.locator('.walkthrough-tabs button').evaluateAll(nodes =>
          nodes.filter(n => n.scrollWidth > n.clientWidth + 1).map(n=>n.textContent));
        assert.deepEqual(clipped, [], `${slug} clipped view buttons at ${width}`);
        if (slug === 'network-io' || slug === 'virtio-balloon') {
          await lab.locator('[data-walkthrough-expand]').click();
          await page.locator(`[data-${prefix}-view="sequence"]`).click();
          await page.screenshot({path:`${shots}/${slug}-${width}.png`});
          await page.keyboard.press('Escape');
        }
        if (slug === 'virtio-balloon') {
          for (const mode of ['inflate','deflate','report','inhibited']) {
            await lab.locator('[data-execution-scenario]').selectOption(mode);
            await lab.locator('[data-execution-view="sequence"]').click();
            await lab.locator('[data-event-id]').last().click();
            const values = await lab.evaluate(node => node.executionSnapshot.frame.values);
            assert.equal(values.actual, ['deflate','report'].includes(mode) ? '0' : '524288');
            assert.equal(values.configured, '8 GiB');
            assert(values.hostRss.includes('需实测'));
          }
        }
        console.log(`PASS event view ${slug} ${width}px`);
      }
      await page.goto(`${base}/versions.html#virtio-specifications`, {waitUntil:'networkidle'});
      const specs = page.locator('#virtio-specifications');
      await specs.scrollIntoViewIfNeeded();
      assert.equal(await page.locator('[data-version-project]').inputValue(), 'virtio');
      assert(await page.locator('[data-version-item][data-project="virtio"]:visible').count() >= 5);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path:`${shots}/versions-${width}.png`});
      await page.goto(`${base}/mechanisms/virtio-net-rx.html`, {waitUntil:'networkidle'});
      await page.locator('a[href="network-io.html?scenario=qemu-rx#walkthrough"]').click();
      assert.equal(await page.locator('[data-execution-lab]').getAttribute('data-scenario'), 'qemu-rx');
      assert.deepEqual(errors, []); await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
