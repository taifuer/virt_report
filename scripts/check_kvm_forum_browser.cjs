/* Optional annual-review audit: two viewports, oldest/newest editions, no external requests. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8092').replace(/\/$/, '');
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'use a local preview only');
const edition = require('../virt_report/content/kvm_forum/2026.json');
const artifacts = path.join(process.env.BROWSER_ARTIFACT_DIR || '/tmp/virt-report-browser', 'kvm-forum');

async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    `horizontal overflow: ${page.url()}`);
}

async function run() {
  fs.mkdirSync(artifacts, {recursive: true});
  const browser = await launchBrowser({headless: true});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  context.setDefaultTimeout(8000);
  context.setDefaultNavigationTimeout(15000);
  await context.route('**/*', route => new URL(route.request().url()).origin === base
    ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  try {
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 960});
      assert.equal((await page.goto(`${base}/kvm-forum.html`)).status(), 200);
      const trends = page.locator('#forum-trends');
      assert(await trends.isVisible(), 'trends are expanded');
      assert.equal(await trends.locator('xpath=ancestor::details').count(), 0);
      assert(await page.evaluate(() => document.querySelector('#forum-trends').compareDocumentPosition(
        document.querySelector('.forum-years')) & Node.DOCUMENT_POSITION_FOLLOWING), 'trends precede all years');
      const selections = page.locator(`#year-${edition.year} .forum-selections a`);
      assert.equal(await selections.count(), edition.review.talks.length);
      assert.equal(await page.locator('ol.forum-selections,ul.forum-selections').count(), 0);
      const cards = page.locator(`#year-${edition.year} .forum-selection`);
      const first = await cards.nth(0).boundingBox(), second = await cards.nth(1).boundingBox();
      if (width > 760) {
        assert(Math.abs(first.y - second.y) <= 1 && second.x > first.x, 'two selections per row');
      } else {
        assert(second.y > first.y && Math.abs(first.x - second.x) <= 1, 'one column on phones');
      }
      await noOverflow(page);
      await page.screenshot({path: path.join(artifacts, `index-${width}.png`)});
      await page.locator(`#year-${edition.year}`).scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(artifacts, `selections-${width}.png`)});
      assert.equal(await page.locator('.forum-page>header .kicker').count(), 0);
      assert.equal(await page.locator('.forum-year h3 a').count(), 17);
      assert(await page.locator('.forum-year h3 a').evaluateAll(nodes => nodes.every(node =>
        getComputedStyle(node).color === getComputedStyle(
          document.querySelector('.forum-selection h4 a')).color)),
      'annual headings use the same green as selection links');
      assert.match(await trends.innerText(), /2026/);
      assert.equal(await page.locator(`#year-${edition.year} .forum-agenda`).innerText(), '原始议程');
      await page.locator(`#year-${edition.year} h3 a[href="kvm-forum/${edition.year}.html"]`).click();
      await page.locator('.forum-review').waitFor();
      const links = page.locator('#mechanism-toc-links');
      assert.equal(await links.locator('a').count(), edition.review.talks.length + 1);
      if (width <= 960) {
        assert(await links.isHidden());
        await page.locator('[data-toc-toggle]').focus();
        await page.keyboard.press('Enter');
        assert(await links.isVisible());
        await page.locator('[data-toc-toggle]').click();
        assert(await links.isHidden());
      } else {
        const text = await page.locator('.mechanism-prose').boundingBox();
        const toc = await page.locator('.mechanism-toc').boundingBox();
        assert(toc.x >= text.x + text.width, 'contents stay on the right');
        assert(await links.locator('a').evaluateAll(nodes => nodes.every(node =>
          getComputedStyle(node).borderLeftWidth === '0px' && getComputedStyle(node).boxShadow === 'none')));
      }
      assert(await page.locator('.mechanism-prose table').count() > 0);
      assert.equal(await page.locator('.forum-talk-source').count(), edition.review.talks.length);
      assert(!(await page.locator('.mechanism-prose').innerText()).includes('重点页码'));
      assert(!(await page.locator('.mechanism-prose').innerText()).includes('**'));
      for (const talk of edition.review.talks) {
        const link = page.locator(`#${talk.id} a[href="${talk.slides_url}"]`);
        assert.equal(await link.count(), 1);
        assert.match(await link.innerText(), new RegExp(talk.slides_format));
        const source = page.locator(`#${talk.id} .forum-talk-source`);
        const titleBox = await source.locator('.forum-source-title').boundingBox();
        const authorBox = await source.locator('.forum-source-author').boundingBox();
        const downloadBox = await source.locator('.forum-source-download').boundingBox();
        assert(authorBox.y >= titleBox.y + titleBox.height, 'authors have a consistent separate row');
        assert.equal(await source.locator('.forum-source-title>.forum-source-download').count(), 1,
          'slides link sits inline after the original title');
        assert(downloadBox.y < authorBox.y, 'slides link stays with title, above author');
        if (width > 620 && talk.id === 'memory-tiering') {
          const original = await source.locator('.forum-source-title>a').first().boundingBox();
          assert(downloadBox.x - original.x - original.width < 25, 'slides link stays beside the short title');
        }
      }
      await noOverflow(page);
      await page.screenshot({path: path.join(artifacts, `review-${width}.png`)});
      // A direct section URL must work after reload; no JS-only routing.
      await page.goto(`${base}/kvm-forum/${edition.year}.html#vgpu-migration`);
      await page.reload();
      assert.equal(new URL(page.url()).hash, '#vgpu-migration');
      await noOverflow(page);
      await page.locator('#vgpu-migration table').scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(artifacts, `table-${width}.png`)});
      await page.locator('#memory-tiering .forum-talk-source').scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(artifacts, `source-${width}.png`)});
      assert.equal((await page.goto(`${base}/kvm-forum/2010.html`)).status(), 200);
      assert.equal(await page.locator('.forum-talk-source').count(), 5);
      assert.equal(await page.locator('.forum-event').innerText(), '5 篇精选');
      assert.equal(await page.locator('#mechanism-toc-links a').count(), 6);
      const oldestSource = page.locator('#qdev .forum-source-title');
      assert.equal(await oldestSource.locator('a').count(), 2);
      assert.match(await oldestSource.innerText(), /qdev.*原始讲稿（PDF）/);
      await noOverflow(page);
      await page.screenshot({path: path.join(artifacts, `historical-${width}.png`)});
      console.log(`PASS KVM Forum hub, 2010/2026 reviews, sources and contents at ${width}px`);
    }
    const plain = await browser.newContext({javaScriptEnabled: false, viewport: {width: 320, height: 900}});
    await plain.route('**/*', route => new URL(route.request().url()).origin === base
      ? route.continue() : route.abort());
    const reader = await plain.newPage();
    await reader.goto(`${base}/kvm-forum/${edition.year}.html`);
    assert(await reader.locator('#mechanism-toc-links').isVisible());
    assert(await reader.locator('[data-toc-toggle]').isHidden());
    await reader.locator('#mechanism-toc-links a[href="#memory-tiering"]').click();
    assert.equal(new URL(reader.url()).hash, '#memory-tiering');
    await noOverflow(reader);
    await plain.close();
    assert.deepEqual(errors, []);
    console.log(`PASS no-JS 320px reader; screenshots: ${artifacts}`);
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'failure.png')}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
