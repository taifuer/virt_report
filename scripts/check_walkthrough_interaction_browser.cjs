/* Local-only: phase jumps, single-step/playback, buffer pointers and explanation alignment. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = process.env.WALKTHROUGH_INTERACTION_SCREENSHOT_DIR || '/tmp/walkthrough-interaction-review';
fs.mkdirSync(shots, {recursive: true});

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function layout(page) {
  const result = await page.evaluate(() => {
    const lab = document.querySelector('[data-walkthrough-lab]');
    const heading = lab.querySelector('.walkthrough-step-heading').getBoundingClientRect();
    const body = lab.querySelector('.walkthrough-step-body').getBoundingClientRect();
    return {width: innerWidth, page: document.documentElement.scrollWidth,
      lab: lab.scrollWidth, client: lab.clientWidth, delta: Math.abs(heading.top - body.top),
      clipped: [...lab.querySelectorAll('.walkthrough-progress button, [data-queue-region]')]
        .filter(n => n.getClientRects().length && n.scrollWidth > n.clientWidth + 1).map(n => n.textContent)};
  });
  assert(result.page <= result.width + 1 && result.lab <= result.client + 1, 'no horizontal overflow');
  assert.deepEqual(result.clipped, []);
  if (result.width >= 1100) assert(result.delta < 1, 'heading and explanation share the same top edge');
}
async function buffers(page, prefix) {
  const structure = prefix === 'vbr' ? 'mechanism' : 'structure';
  await page.locator(`[data-${prefix}-view="${structure}"]`).click();
  // Advance once if needed, until the typed snapshot has addresses.
  await page.evaluate(prefix => {
    for (let n = 0; !document.querySelector('[data-typed-queue] table button[data-buffer-ref]') && n < 50; n++)
      document.querySelector(`[data-${prefix}-next]`).click();
  }, prefix);
  const root = page.locator('[data-typed-queue]:visible').last();
  assert.deepEqual(await root.locator('[data-queue-region]').evaluateAll(nodes => nodes.map(n => n.dataset.queueRegion)),
    ['descriptors', 'buffers', 'avail', 'used']);
  const link = root.locator('table button[data-buffer-ref]').first();
  const target = await link.getAttribute('data-buffer-ref');
  await link.focus(); await page.keyboard.press('Enter');
  assert.equal(await root.locator(`[data-buffer="${target}"]`).getAttribute('data-selected'), 'true');
  assert.equal(await link.getAttribute('aria-pressed'), 'true');
  const other = root.locator('[data-buffer] button[data-buffer-ref]').last();
  const next = await other.getAttribute('data-buffer-ref'); await other.click();
  assert.equal(await root.locator(`[data-buffer="${next}"]`).getAttribute('data-selected'), 'true');
  assert.equal(await root.locator('[data-buffer][data-selected="true"]').count(), 1);
  await layout(page);
}

(async () => {
  const browser = await launchBrowser({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: width > 700 ? 1000 : 844});
      for (const backend of ['local-blk', 'local-scsi', 'rbd-blk', 'spdk-blk', 'spdk-scsi']) {
        await page.goto(`${base}/mechanisms/virtio-blk-read.html?backend=${backend}`);
        await page.locator('[data-vbr-progress] button').first().waitFor();
        const sequence = await page.evaluate(() => {
          const baseScene = JSON.parse(document.querySelector('#virtio-blk-read-data').textContent);
          const catalog = JSON.parse(document.querySelector('#block-io-scenarios-data').textContent);
          const lab = document.querySelector('[data-vbr-lab]');
          const scene = VirtioBlkReadModel.getScenario(baseScene, 'read', lab.dataset.backend, catalog);
          const frames = VirtioBlkReadModel.buildTrace(scene), results = [];
          const buttons = [...lab.querySelectorAll('[data-vbr-phase]')];
          for (const button of buttons) {
            button.click();
            const peers = frames.filter(f => f.phase === button.dataset.vbrPhase);
            for (let n = 0; n < peers.length; n++) {
              results.push({expected: peers[n].id, actual: lab.dataset.step,
                progress: parseFloat(button.style.getPropertyValue('--phase-progress')),
                expectedProgress: 100 * (n + 1) / peers.length,
                counter: lab.querySelector('[data-vbr-counter]').textContent, fraction: `步骤 ${n + 1} / ${peers.length}`});
              if (n < peers.length - 1) lab.querySelector('[data-vbr-next]').click();
            }
          }
          return results;
        });
        for (const item of sequence) {
          assert.equal(item.actual, item.expected, `${backend}: clicking and stepping use the same trace`);
          assert(Math.abs(item.progress - item.expectedProgress) < .01, `${backend}: ${JSON.stringify(item)}`);
          assert(item.counter.includes(item.fraction));
        }
        await layout(page);
        await buffers(page, 'vbr');
        if (backend === 'spdk-blk') {
          await settle(page);
          await page.locator('[data-vbr-structure]').screenshot({path: `${shots}/disk-buffers-${width}.png`});
          await page.locator('[data-vbr-view="overview"]').click();
          const phase = page.locator('[data-vbr-phase]').nth(3);
          await phase.click();
          const first = await page.locator('[data-vbr-lab]').getAttribute('data-step');
          await page.locator('[data-vbr-next]').click();
          const next = await page.locator('[data-vbr-lab]').getAttribute('data-step');
          await phase.click();
          assert.equal(await page.locator('[data-vbr-lab]').getAttribute('data-step'), first);
          await page.locator('[data-vbr-speed]').selectOption('1200');
          await page.locator('[data-vbr-play]').click();
          await page.waitForFunction(first => document.querySelector('[data-vbr-lab]').dataset.step !== first, first);
          await page.locator('[data-vbr-play]').click();
          assert.equal(await page.locator('[data-vbr-lab]').getAttribute('data-step'), next, 'playback and next show the same operation');
          await phase.click(); await phase.scrollIntoViewIfNeeded(); await settle(page);
          await page.screenshot({path: `${shots}/disk-phase-${width}.png`});
        }
      }
      await page.goto(`${base}/mechanisms/network-io.html`);
      await page.locator('[data-execution-next]').waitFor();
      const scenarios = await page.evaluate(() => JSON.parse(document.querySelector('#execution-demo-data').textContent).scenarios.map(s => s.id));
      assert.equal(scenarios.length, 8);
      for (const scenario of scenarios) {
        await page.goto(`${base}/mechanisms/network-io.html?scenario=${scenario}`);
        await page.locator('[data-execution-next]').waitFor();
        assert.equal(await page.locator('[data-execution-scenario-choice]').isVisible(), false);
        assert.equal(await page.locator('[data-execution-branch], [data-execution-order]').count(), 0);
        await page.locator('[data-execution-phase]').nth(2).click();
        await layout(page); await buffers(page, 'execution');
        if (scenario === 'qemu-tx') {
          await page.locator('[data-execution-view="overview"]').click();
          await page.locator('[data-execution-phase="consume"]').click();
          await page.locator('[data-execution-next]').click();
          await page.locator('.walkthrough-selection').scrollIntoViewIfNeeded(); await settle(page);
          await page.screenshot({path: `${shots}/network-tx-${width}.png`});
        }
      }
      console.log(`PASS ${width}px: disk phase steps/progress/playback, four memory regions and address links; eight linear network scenarios`);
    }
    assert.deepEqual(errors, []);
    console.log(`PASS interaction review; screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
