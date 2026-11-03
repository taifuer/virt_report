/* Focus-layout regression checks. Requires Playwright and a local preview. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const slugs = ['virtio-blk-read', 'network-io', 'cpu-execution', 'memory-access',
  'interrupt-delivery', 'gpu-execution', 'virtio-gpu-execution'];
const assets = path.resolve(__dirname, '../virt_report/render/assets');

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  // Use edited shared assets without requiring a full site export during work.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
    const name = path.basename(url.pathname);
    if (['walkthrough.css', 'walkthrough-fullscreen.js', 'execution-demo.css',
      'execution-demo.js', 'walkthrough-components.js'].includes(name)) {
      return route.fulfill({status: 200, body: fs.readFileSync(path.join(assets, name)),
        contentType: name.endsWith('.css') ? 'text/css' : 'application/javascript'});
    }
    return route.continue();
  });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const [width, height] of [[1366, 768], [1920, 1080], [768, 1024], [390, 844], [320, 720]]) {
      await page.setViewportSize({width, height});
      for (const slug of slugs) {
        await page.goto(`${base}/mechanisms/${slug}.html`);
        const lab = page.locator('[data-walkthrough-lab]').first();
        const expand = lab.locator('[data-walkthrough-expand]').first();
        const views = lab.locator('.walkthrough-tabs button');
        await expand.click();
        await page.waitForFunction(() => document.querySelector('[data-walkthrough-lab]').classList.contains('walkthrough-expanded'));
        for (let view = 0; view < 3; view++) {
          await views.nth(view).click();
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const result = await lab.evaluate(node => {
            const workspace = node.querySelector('.walkthrough-workspace');
            const canvas = workspace.querySelector('.execution-canvas, .vbr-visual');
            const inspector = workspace.querySelector('.walkthrough-inspector');
            const rect = element => {
              const r = element.getBoundingClientRect();
              return {top: r.top, left: r.left, right: r.right, bottom: r.bottom, height: r.height};
            };
            const progress = node.querySelector('.walkthrough-progress');
            return {width: innerWidth, scrollWidth: node.scrollWidth, canvas: rect(canvas),
              inspector: rect(inspector), summary: getComputedStyle(node.querySelector('.walkthrough-summary')).display,
              rows: new Set([...progress.querySelectorAll('button')].map(button => Math.round(button.getBoundingClientRect().top))).size,
              progress: rect(progress), wrap: getComputedStyle(progress).flexWrap,
              progressFits: progress.scrollWidth <= progress.clientWidth + 1,
              stepsContained: [...progress.querySelectorAll('button')].every(button => {
                const parent = progress.getBoundingClientRect(), child = button.getBoundingClientRect();
                return child.left >= parent.left - 1 && child.right <= parent.right + 1
                  && child.top >= parent.top - 1 && child.bottom <= parent.bottom + 1;
              }),
              usable: [...node.querySelectorAll('.walkthrough-controls button, .walkthrough-tabs button')]
                .filter(button => button.getClientRects().length)
                .every(button => button.getBoundingClientRect().height >= 44),
              canvasScrollable: canvas.scrollHeight <= canvas.clientHeight + 1 || getComputedStyle(canvas).overflowY === 'auto'};
          });
          assert(result.scrollWidth <= result.width + 1, `${slug}/${width}/${view}: horizontal overflow ${JSON.stringify(result)}`);
          assert.equal(result.summary, 'none');
          assert.equal(result.wrap, 'wrap', `${slug}: steps should wrap without a horizontal strip`);
          assert(result.progressFits && result.stepsContained, `${slug}: every step fits the wrapped list`);
          assert(result.usable, `${slug}: controls retain 44px hit targets`);
          if (width >= 1100) {
            assert(result.canvas.top >= result.progress.bottom && result.canvas.top - result.progress.bottom < 24,
              `${slug}: drawing follows the complete wrapped steps ${JSON.stringify(result)}`);
            assert(Math.abs(result.canvas.top - result.inspector.top) < 2, `${slug}: explanation beside drawing`);
            assert(result.canvas.right <= result.inspector.left, `${slug}: no overlapping columns`);
            assert(result.canvas.bottom <= height + 2, `${slug}: drawing panel fits viewport ${JSON.stringify(result)}`);
            assert(result.canvasScrollable, `${slug}: long source/structure remains reachable`);
          }
        }
        // Selection does not horizontally scroll or clip any wrapped step.
        await views.first().click();
        const buttons = lab.locator('.walkthrough-progress button');
        await buttons.last().evaluate(button => button.click());
        await page.waitForTimeout(40);
        const revealed = await lab.locator('.walkthrough-progress').evaluate(strip => {
          const r = strip.getBoundingClientRect(), c = strip.querySelector('[aria-current="step"]').getBoundingClientRect();
          return strip.scrollLeft === 0 && c.left >= r.left - 1 && c.right <= r.right + 1
            && c.top >= r.top - 1 && c.bottom <= r.bottom + 1;
        });
        assert(revealed, `${slug}: selected step remains in its wrapped row`);
        if (width === 1366 && ['network-io', 'interrupt-delivery', 'virtio-blk-read'].includes(slug)) {
          await lab.evaluate(node => { node.scrollTop = 0; });
          await page.screenshot({path: `/tmp/wrapped-steps-${slug}-${width}.png`});
        }
        await expand.click();
        await page.waitForFunction(() => !document.querySelector('[data-walkthrough-lab]').classList.contains('walkthrough-expanded'));
        assert(await expand.evaluate(button => document.activeElement === button));
        assert(await lab.locator('.walkthrough-summary').isVisible());
      }
      // Routing needs a gutter around the full-width Guest RAM heading/status.
      // Verify an actual cross-memory handoff, not just an empty initial view.
      await page.goto(`${base}/mechanisms/network-io.html?scenario=qemu-tx&view=overview&step=publish`);
      const network = page.locator('[data-walkthrough-lab]').first();
      await network.locator('[data-walkthrough-expand]').first().click();
      await page.waitForFunction(() => {
        const lab = document.querySelector('[data-walkthrough-lab]');
        return lab.classList.contains('walkthrough-expanded') && lab.dataset.step === 'publish'
          && lab.querySelectorAll('.execution-arrows path').length >= 2;
      });
      const route = await network.evaluate(lab => {
        const paths = [...lab.querySelectorAll('.execution-arrows path')];
        return {gutter: parseFloat(getComputedStyle(lab.querySelector('.execution-nodes')).paddingLeft),
          paths: paths.map(path => path.getAttribute('d'))};
      });
      assert(route.gutter >= 8, `network/${width}: preserve the outer routing channel`);
      assert(route.paths.every(path => /^M .+ L /.test(path)), `network/${width}: visible publish handoff`);
      await network.locator('[data-walkthrough-expand]').first().click();
      console.log(`PASS expanded controls, wrapped steps, all views and focus: ${width}x${height}`);
    }
    // API-less fullscreen uses the same layout and restores page access on Escape.
    await page.goto(`${base}/mechanisms/network-io.html`);
    await page.locator('[data-walkthrough-lab]').first().evaluate(node => { node.requestFullscreen = undefined; });
    const expand = page.locator('[data-walkthrough-expand]').first();
    await expand.click();
    assert.equal(await page.locator('[data-walkthrough-lab]').first().getAttribute('data-walkthrough-expanded'), 'fallback');
    await page.keyboard.press('Escape');
    assert.equal(await expand.getAttribute('aria-pressed'), 'false');
    assert.equal(await page.locator('[inert]').count(), 0);
    assert(await expand.evaluate(button => document.activeElement === button));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
