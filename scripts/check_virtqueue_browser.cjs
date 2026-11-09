/* Compare the same requests in two layouts; only local static assets are used. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.VIRTQUEUE_SCREENSHOT_DIR || '/tmp/virtqueue-review';
(async () => {
  fs.mkdirSync(shots, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const context = await browser.newContext({reducedMotion: 'reduce'});
    await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/mechanisms/packed-virtqueue.html#interactive`);
    const lab = page.locator('[data-packed-lab]');
    const action = name => lab.locator(`[data-packed-action="${name}"]`);
    const format = name => lab.locator(`[data-queue-format="${name}"]`);
    const message = lab.locator('[data-packed-status]');
    assert.equal(await lab.getAttribute('data-queue-format'), 'split');
    assert.equal(await lab.locator('.is-available,.is-completed').count(), 0);
    assert(await action('complete').isDisabled());
    assert(await action('reclaim').isDisabled());
    await action('publish').focus(); await page.keyboard.press('Enter');
    assert.match(await message.textContent(), /desc\[2\][\s\S]*avail.idx=1/);
    const splitMessage = await message.textContent();
    await format('packed').click();
    assert.match(await message.textContent(), /Buffer ID=2[\s\S]*A\/U=1\/0/);
    assert(await lab.locator('[data-split-rings]').isHidden());
    await format('split').click(); assert.equal(await message.textContent(), splitMessage);
    for (let i = 0; i < 3; i++) await action('publish').click();
    assert(await action('publish').isDisabled());
    await action('complete').click();
    assert.match(await message.textContent(), /id:2, len:64/);
    assert(await action('publish').isDisabled(), 'completion alone does not return ownership');
    await action('reclaim').click();
    assert(await action('publish').isEnabled());
    assert.match(await lab.locator('[data-split-rings]').textContent(), /已消费的旧值/);
    await action('publish').click();
    assert.match(await message.textContent(), /avail.ring\[0\]=2[\s\S]*avail.idx=5/);
    await format('packed').click();
    assert.match(await message.textContent(), /A\/U=0\/1/);
    await action('reset').click();
    assert.equal(await lab.locator('.is-available,.is-completed').count(), 0);
    await format('split').click(); assert.match(await message.textContent(), /avail.idx=0/);
    await action('publish').click(); await action('complete').click();
    for (const width of [320, 390, 768, 1366]) {
      await page.setViewportSize({width, height: 900});
      for (const name of ['split', 'packed']) {
        await format(name).click();
        await lab.scrollIntoViewIfNeeded();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        assert(await lab.evaluate(node => node.scrollWidth <= node.clientWidth + 1), `${name} overflow at ${width}`);
        await lab.screenshot({path: path.join(shots, `${name}-${width}.png`)});
      }
    }
    await page.setViewportSize({width: 1366, height: 900});
    const expand = lab.locator('[data-mechanism-expand]');
    await expand.click();
    assert(await page.locator('dialog[open] [data-viewer-action="close"]').isVisible());
    assert(await lab.evaluate(node => node.closest('dialog')?.open));
    await action('reset').click();
    await action('publish').click();
    await page.keyboard.press('Escape');
    await page.locator('dialog[open]').waitFor({state: 'hidden'});
    await page.waitForFunction(() => !document.querySelector('[data-packed-lab]').closest('dialog'));
    assert(await expand.evaluate(node => document.activeElement === node));
    assert.match(await message.textContent(), /发布请求 1/);
    assert.deepEqual(errors, []);
    console.log('PASS Split/Packed comparison, ownership, wrap, keyboard and responsive layout');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
