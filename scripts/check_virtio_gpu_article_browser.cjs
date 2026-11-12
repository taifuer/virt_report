/* Check the consolidated GPU article, its inline lab and old bookmarks. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const origin = new URL(base).origin;
const shots = process.env.GPU_SCREENSHOT_DIR;
if (shots) fs.mkdirSync(shots, {recursive: true});
const title = 'virtio-gpu：资源、渲染与编解码';
const oldRendering = require('../virt_report/content/mechanisms.json').redirects['virtio-gpu-rendering'];

(async () => {
  const browser = await launchBrowser({headless: true});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  // The check only visits the supplied preview or deployment, never source links.
  await context.route('**/*', route => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [320, 390, 1440]) {
      await page.setViewportSize({width, height: 1000});
      const response = await page.goto(`${base}/mechanisms/virtio-gpu.html`);
      assert.equal(response.status(), 200);
      assert.equal(await page.locator('h1').textContent(), title);
      assert.equal(await page.locator('[data-mechanism-view="diagram"]').count(), 6);
      assert.equal(await page.locator('[data-execution-lab]').count(), 1);
      const lab = page.locator('[data-virtio-gpu-lab]');
      await lab.scrollIntoViewIfNeeded();
      await lab.locator('[data-gpu-controls]').waitFor({state: 'visible'});
      assert(await lab.locator('[data-gpu-back]').isDisabled());
      for (let step = 1; step <= 10; step++) {
        await lab.locator('[data-gpu-next]').click();
        assert((await lab.locator('[data-gpu-status]').textContent()).includes(`第 ${step} / 10 步`));
        if (step === 7) {
          assert.equal(await lab.locator('[data-gpu-bytes]').textContent(), '32 KiB');
          assert.equal(await lab.locator('[data-gpu-image="host"]').getAttribute('data-version'), 'B');
          assert.equal(await lab.locator('[data-gpu-image="display"]').getAttribute('data-version'), 'A');
        }
        if (step === 9) {
          assert.equal(await lab.locator('[data-gpu-image="display"]').getAttribute('data-version'), 'A');
          assert((await lab.locator('[data-gpu-response]').textContent()).includes('不代表物理呈现完成'));
        }
      }
      assert(await lab.locator('[data-gpu-next]').isDisabled());
      assert.equal(await lab.locator('[data-gpu-image="display"]').getAttribute('data-version'), 'B');
      if (shots) await lab.screenshot({path: path.join(shots, `article-2d-${width}.png`)});
      await lab.locator('[data-gpu-back]').click();
      assert.equal(await lab.locator('[data-gpu-image="display"]').getAttribute('data-version'), 'A');
      await lab.locator('[data-gpu-size]').selectOption('large');
      assert(await lab.locator('[data-gpu-back]').isDisabled());
      for (let step = 1; step <= 7; step++) await lab.locator('[data-gpu-next]').click();
      assert.equal(await lab.locator('[data-gpu-bytes]').textContent(), '128 KiB');
      await lab.locator('[data-gpu-reset]').click();
      assert(await lab.locator('[data-gpu-back]').isDisabled());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        `${width}px: no page overflow`);
      await page.evaluate(() => scrollTo(0, 0));
      if (shots) await page.screenshot({path: path.join(shots, `article-heading-${width}.png`)});
      console.log(`PASS consolidated article and 2D lab: ${width}px`);
    }
    for (const anchor of ['preface', 'transport', 'render', 'video-support', 'decode',
      'encode', 'completion', 'evolution', 'sources', 'transport-part-1', 'render-part-4']) {
      await page.goto(`${base}/mechanisms/virtio-gpu-rendering.html#${anchor}`);
      const canonicalAnchor = oldRendering.anchors[anchor] || anchor;
      await page.waitForURL(`${base}/mechanisms/virtio-gpu.html#${canonicalAnchor}`);
      assert.equal(await page.locator(`[id="${anchor}"]`).count(), 1);
      assert.equal(await page.locator(`[id="${canonicalAnchor}"]`).count(), 1);
    }
    for (const anchor of ['commands', 'interactive', 'summary', 'references']) {
      await page.goto(`${base}/mechanisms/virtio-gpu.html#${anchor}`);
      assert.equal(await page.locator(`[id="${anchor}"]`).count(), 1);
    }
    await page.goto(`${base}/mechanisms.html#device-virtualization`);
    assert.equal(await page.locator('a[href="mechanisms/virtio-gpu.html"]').count(), 1);
    assert.equal(await page.locator('a[href="mechanisms/virtio-gpu-rendering.html"]').count(), 0);
    assert.equal(await page.locator('a[href="mechanisms/virtio-gpu-execution.html"]').count(), 0);
    assert.equal(await page.locator('a[href="mechanisms/gpu-execution.html"]').count(), 1);
    if (shots) await page.locator('#device-virtualization').screenshot({
      path: path.join(shots, 'article-directory.png')
    });
    assert.deepEqual(errors, []);
    console.log('PASS legacy URLs/fragments, unique catalogue entries and no runtime errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
