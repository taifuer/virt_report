/* Local-only reading, navigation and responsive checks for the upgrade articles. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = '/tmp/kvm-upgrade-article-review';
fs.mkdirSync(shots, {recursive: true});

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const errors = [];
  try {
    for (const javaScriptEnabled of [true, false]) {
      const context = await browser.newContext({javaScriptEnabled, reducedMotion: 'reduce'});
      await context.route('**/*', route => route.request().url().startsWith(base + '/')
        ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      for (const width of [1440, 1024, 768, 390, 320]) {
        await page.setViewportSize({width, height: 1000});
        for (const slug of ['qemu-cpr-transfer', 'kvm-live-upgrade']) {
          const response = await page.goto(`${base}/mechanisms/${slug}.html`);
          assert.equal(response.status(), 200);
          assert.equal(await page.locator('h1').count(), 1);
          const layout = await page.evaluate(() => ({width: innerWidth,
            scroll: document.documentElement.scrollWidth,
            missing: [...document.querySelectorAll('.mechanism-toc a[href^="#"]')]
              .map(link => link.getAttribute('href').slice(1))
              .filter(id => !document.getElementById(id)),
          }));
          assert(layout.scroll <= layout.width + 1, `${slug}/${width}: horizontal overflow`);
          assert.deepEqual(layout.missing, [], `${slug}: all article anchors resolve`);
          if (slug === 'kvm-live-upgrade') {
            await page.locator('.mechanism-toc a[href="#caretaker"]').click();
            assert.equal(new URL(page.url()).hash, '#caretaker');
            assert((await page.locator('#caretaker').textContent()).includes('不能处理的退出'));
            await page.goto(`${base}/mechanisms/${slug}.html`);
            if (javaScriptEnabled && [1440, 390].includes(width)) {
              await page.screenshot({path: `${shots}/kvm-upgrade-${width}.png`, fullPage: true});
            }
          }
        }
        console.log(`PASS upgrade articles: ${width}px, JavaScript ${javaScriptEnabled}`);
      }
      await page.goto(`${base}/mechanisms/qemu-cpr-transfer.html`);
      await page.locator('#preface a[href="kvm-live-upgrade.html"]').click();
      assert(new URL(page.url()).pathname.endsWith('/kvm-live-upgrade.html'));
      await page.locator('#preface a[href="qemu-cpr-transfer.html"]').click();
      assert(new URL(page.url()).pathname.endsWith('/qemu-cpr-transfer.html'));
      await context.close();
    }
    assert.deepEqual(errors, []);
    console.log(`PASS cross-links, anchors, offline reading and responsive layouts; screenshots: ${shots}`);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
