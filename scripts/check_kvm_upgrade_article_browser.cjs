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
            await page.locator('.mechanism-toc a[href="#device-handover"]').click();
            assert.equal(new URL(page.url()).hash, '#device-handover');
            assert((await page.locator('#device-handover').textContent()).includes('VFIO connector'));
            const diagrams = page.locator('.mechanism-diagram img');
            assert.equal(await diagrams.count(), 5);
            for (const img of await diagrams.all()) {
              await img.scrollIntoViewIfNeeded();
              // Native lazy-loading may start after the scroll completes.
              // Wait for that request before asking Chromium to decode it.
              const src = await img.getAttribute('src');
              await page.waitForFunction(source => [...document.images].some(node =>
                node.getAttribute('src') === source && node.complete && node.naturalWidth > 0),
              src, {timeout: 10000});
              await img.evaluate(node => node.decode());
              assert(await img.evaluate(node => node.complete && node.naturalWidth > 0));
            }
            for (const section of ['evolution', 'hyperfresh', 'compatibility']) {
              await page.locator(`.mechanism-toc a[href="#${section}"]`).click();
              assert.equal(new URL(page.url()).hash, `#${section}`);
              assert(await page.locator(`#${section}`).isVisible());
            }
            await page.goto(`${base}/mechanisms/${slug}.html`);
            if (javaScriptEnabled && [1440, 390].includes(width)) {
              await page.screenshot({path: `${shots}/kvm-upgrade-${width}.png`, fullPage: true});
              for (const name of ['orthus-dual-kvm', 'orthus-vm-grafting', 'orthus-device-handover',
                'kvm-upgrade-vhost-handover', 'hyperfresh-handoff']) {
                const figure = page.locator(`[data-diagram="${name}"]`);
                await figure.scrollIntoViewIfNeeded();
                await figure.screenshot({path: `${shots}/${name}-${width}.png`});
                await figure.locator('[data-mechanism-expand]').click();
                const viewer = page.locator('dialog.mechanism-viewer[open]');
                assert.equal(await viewer.count(), 1);
                await viewer.locator('img').evaluate(img => img.decode());
                assert(await viewer.locator('img').evaluate(img => img.complete && img.naturalWidth > 0));
                await viewer.getByRole('button', {name: '退出全屏', exact: true}).click();
                assert.equal(await page.locator('dialog.mechanism-viewer[open]').count(), 0);
              }
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
      if (javaScriptEnabled) {
        for (const width of [1440, 390]) {
          await page.setViewportSize({width, height: 1000});
          await page.goto(`${base}/conference-papers.html?q=Orthus`);
          const orthus = page.locator('#paper-asplos19-orthus');
          await orthus.waitFor({state: 'visible'});
          assert.equal(await orthus.locator('h2 a').getAttribute('href'),
            'https://doi.org/10.1145/3297858.3304034');
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
          await orthus.screenshot({path: `${shots}/orthus-paper-${width}.png`});
          await page.goto(`${base}/mechanisms/kvm-live-upgrade.html#sources`);
          await page.locator('#sources a[href*="?q=HyperFresh"]').click();
          assert.equal(new URL(page.url()).searchParams.get('q'), 'HyperFresh');
          const hyperfresh = page.locator('#paper-vee19-live-replacement');
          await hyperfresh.waitFor({state: 'visible'});
          assert((await hyperfresh.textContent()).includes('L0 hyperplexor'));
          assert.equal(await hyperfresh.locator('h2 a').getAttribute('href'),
            'https://doi.org/10.1145/3313808.3313821');
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
          await hyperfresh.screenshot({path: `${shots}/hyperfresh-paper-${width}.png`});
        }
      }
      await context.close();
    }
    assert.deepEqual(errors, []);
    console.log(`PASS cross-links, anchors, offline reading and responsive layouts; screenshots: ${shots}`);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
