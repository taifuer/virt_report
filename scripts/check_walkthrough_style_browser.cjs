/* Shared appearance/fullscreen regressions across the five offline demos. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const slugs = ['virtio-blk-read', 'network-io', 'cpu-execution', 'memory-access', 'interrupt-delivery'];
const shots = process.env.WALKTHROUGH_SCREENSHOT_DIR;
if (shots) fs.mkdirSync(shots, {recursive: true});

async function assertDomainAlignment(page, context, requireInlineQemu = false) {
  const domains = await page.locator('.walkthrough-domain').evaluateAll(nodes => nodes.map(domain => {
    const rect = node => { const r = node.getBoundingClientRect(); return {top:r.top,bottom:r.bottom,left:r.left,right:r.right}; };
    return {id: domain.dataset.executionDomain, title: domain.querySelector('h4')?.textContent,
      header: rect(domain.querySelector('header')), cards: [...domain.querySelectorAll('[data-execution-node]')].map(rect)};
  }));
  for (const left of domains) for (const right of domains) {
    if (left === right || Math.abs(left.header.top - right.header.top) > 2) continue;
    assert(Math.abs(left.header.bottom - right.header.bottom) <= 2,
      `${context}: headers in one visual row must align ${JSON.stringify({left,right})}`);
    assert(Math.abs(left.cards[0].top - right.cards[0].top) <= 2,
      `${context}: cards below peer headers must align ${JSON.stringify({left,right})}`);
  }
  if (requireInlineQemu) {
    const qemu = domains.find(domain => domain.title === 'QEMU');
    assert(qemu && qemu.cards.length === 2, `${context}: expected both QEMU components`);
    assert(Math.abs(qemu.cards[0].top - qemu.cards[1].top) <= 2,
      `${context}: QEMU components should be side by side ${JSON.stringify(qemu)}`);
    assert(qemu.cards[0].right <= qemu.cards[1].left + 1 || qemu.cards[1].right <= qemu.cards[0].left + 1,
      `${context}: QEMU cards must not overlap ${JSON.stringify(qemu)}`);
  }
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}, reducedMotion: 'reduce'});
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: 1100});
      let standard;
      for (const slug of slugs) {
        await page.goto(`${base}/mechanisms/${slug}.html`);
        await page.locator('.walkthrough-selection').first().waitFor({state: 'visible'});
        const result = await page.evaluate(() => {
          const lab = document.querySelector('[data-walkthrough-lab]');
          const style = node => {
            const c = getComputedStyle(node);
            return {height: node.getBoundingClientRect().height, fontSize: c.fontSize,
              fontWeight: c.fontWeight, radius: c.borderRadius, color: c.color, background: c.backgroundColor};
          };
          const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
          const selects = [...lab.querySelectorAll('.walkthrough-choice select')].filter(visible);
          const select = selects[0];
          if (!select) throw new Error('The walkthrough must expose a primary selector');
          const speed = lab.querySelector('.walkthrough-controls select');
          const selector = lab.querySelector('.walkthrough-selection').getBoundingClientRect(), toolbar = lab.querySelector('.walkthrough-toolbar').getBoundingClientRect();
          const summary = lab.querySelector('.walkthrough-summary').getBoundingClientRect();
          const config = lab.querySelector('.walkthrough-configuration').getBoundingClientRect();
          const board = lab.querySelector('.walkthrough-workspace').getBoundingClientRect();
          const domainNodes = [...lab.querySelectorAll('[data-execution-node]')];
          return {page: document.documentElement.scrollWidth, width: innerWidth,
            base: {select: style(select), speed: style(speed), play: style(lab.querySelector('[data-walkthrough-play]')),
              tab: style(lab.querySelector('.walkthrough-tabs button')), expand: style(lab.querySelector('[data-walkthrough-expand]'))},
            arrows: [...selects, speed].map(node => ({image: getComputedStyle(node).backgroundImage, padding: parseFloat(getComputedStyle(node).paddingRight)})),
            selectorGap: toolbar.top - selector.bottom, summaryHeight: summary.height, configBelow: config.top >= board.bottom,
            allDomains: domainNodes.every(node => node.closest('.walkthrough-domain, .walkthrough-memory')),
            controlsContained: [...lab.querySelectorAll('.walkthrough-toolbar button, .walkthrough-toolbar select')].every(node => {
              const r = node.getBoundingClientRect(); return r.left >= toolbar.left - 1 && r.right <= toolbar.right + 1;
            })};
        });
        assert(result.page <= width + 1, `${slug}/${width}: overflow ${JSON.stringify(result)}`);
        assert(result.arrows.every(x => x.image.includes('data:image/svg+xml') && x.padding >= 23), `${slug}: visible select arrows`);
        assert(result.controlsContained && result.configBelow && result.allDomains, `${slug}/${width}: layout ${JSON.stringify(result)}`);
        assert(result.selectorGap < result.summaryHeight + 45, `${slug}: controls separated by configuration text ${JSON.stringify(result)}`);
        if (width >= 768 && slug !== 'virtio-blk-read') await assertDomainAlignment(page, `${slug}/${width}`);
        if (!standard) standard = result.base;
        else assert.deepEqual(result.base, standard, `${slug}/${width}: shared control styling ${JSON.stringify({actual: result.base, expected: standard})}`);
        const expand = page.locator('[data-walkthrough-expand]').first();
        const lab = page.locator('[data-walkthrough-lab]').first();
        const step = await lab.getAttribute('data-step');
        await expand.click();
        await page.waitForFunction(() => document.querySelector('[data-walkthrough-expand]').getAttribute('aria-pressed') === 'true');
        assert.equal(await lab.getAttribute('data-step'), step);
        await expand.click();
        await page.waitForFunction(() => document.querySelector('[data-walkthrough-expand]').getAttribute('aria-pressed') === 'false');
        if (shots && [1440, 390].includes(width)) {
          await page.locator('.walkthrough-selection').first().evaluate(node => window.scrollTo(0, node.getBoundingClientRect().top + window.scrollY - 80));
          await page.screenshot({path: path.join(shots, `${slug}-${width}.png`)});
        }
      }
      if (width >= 390) {
        for (const [slug, scene] of [['cpu-execution','mmio-userspace'], ['memory-access','mmio-read'],
          ['interrupt-delivery','msix-software'], ['interrupt-delivery','msix-masked']]) {
          await page.goto(`${base}/mechanisms/${slug}.html?scenario=${scene}`);
          await page.locator('.walkthrough-domain').first().waitFor({state: 'visible'});
          await assertDomainAlignment(page, `${slug}/${scene}/${width}`, true);
        }
      }
      console.log(`PASS shared layout, select arrows, controls and fullscreen: ${width}px`);
    }
    // The fallback works for both engines and restores isolation/focus on Escape.
    for (const slug of ['virtio-blk-read', 'network-io']) {
      await page.goto(`${base}/mechanisms/${slug}.html`);
      await page.locator('[data-walkthrough-lab]').first().evaluate(node => { node.requestFullscreen = undefined; });
      await page.locator('[data-walkthrough-expand]').first().click();
      assert.equal(await page.locator('[data-walkthrough-lab]').first().getAttribute('data-walkthrough-expanded'), 'fallback');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-walkthrough-expand]').first().getAttribute('aria-pressed'), 'false');
      assert(await page.locator('[data-walkthrough-expand]').first().evaluate(node => node === document.activeElement));
      assert.equal(await page.locator('[inert]').count(), 0);
    }
    assert.deepEqual(errors, []);
    console.log('PASS shared native/fallback fullscreen, preserved state and focus');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
