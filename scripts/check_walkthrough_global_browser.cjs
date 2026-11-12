/* Cross-demo presentation audit. Offline models and local preview only. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const shots = process.env.WALKTHROUGH_GLOBAL_SCREENSHOT_DIR || '/tmp/walkthrough-global-review';
fs.mkdirSync(shots, {recursive: true});
const pages = ['virtio-blk-read', 'network-io', 'cpu-execution', 'memory-access',
  'interrupt-delivery', 'gpu-execution', 'virtio-gpu-execution', 'libvirt-domain-start'];

function inspect() {
  const lab = document.querySelector('[data-walkthrough-lab]');
  const visible = n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden';
  const clipped = [...lab.querySelectorAll(`.walkthrough-progress button, .execution-node-heading,
    .vbr-node-heading, .network-memory-panel, .walkthrough-state-grid > div,
    [data-component-header], .walkthrough-domain-note`)]
    .filter(visible).filter(n => n.scrollWidth > n.clientWidth + 1).map(n => n.textContent);
  const visual = lab.querySelector('.vbr-visual, .execution-canvas');
  return {page: document.documentElement.scrollWidth, width: innerWidth,
    lab: lab.scrollWidth, client: lab.clientWidth, clipped,
    visual: visual.scrollWidth, visualClient: visual.clientWidth,
    explanationLinks: lab.querySelectorAll('.walkthrough-inspector a').length,
    expanded: lab.querySelector('[data-walkthrough-expand]').textContent,
    cpu: [...lab.querySelectorAll('[data-cpu-field]')].map(n => ({key: n.dataset.cpuField,
      now: n.querySelector('strong').textContent, before: n.querySelector('.walkthrough-value-before')?.textContent})),
    snapshot: lab.executionSnapshot ? {lane: lab.executionSnapshot.frame.execution?.lane,
      fields: lab.executionSnapshot.frame.values} : null,
    activeLane: lab.querySelector('[data-cpu-lane][data-current="true"]')?.dataset.cpuLane,
    currentLane: lab.querySelector('[data-cpu-lane][aria-current="location"]')?.dataset.cpuLane,
    inactiveStatuses: lab.querySelectorAll('[data-cpu-lane][data-current="false"] .execution-node-status').length,
    view: lab.dataset.view,
    components: [...lab.querySelectorAll('[data-component-layout]')].filter(visible).map(layout => ({
      columns: [...layout.children].map(column => {
        const heading = column.querySelector('[data-component-header] h4').getBoundingClientRect();
        const body = column.querySelector('.walkthrough-domain-body').getBoundingClientRect();
        const box = column.getBoundingClientRect();
        return {id: column.dataset.componentColumn,
          title: column.querySelector('[data-component-header] h4').textContent,
          left: box.left, width: box.width, titleTop: heading.top, bodyTop: body.top};
      }),
      misplaced: [...layout.querySelectorAll('.walkthrough-domain-extension')].some(node =>
        node.parentElement.closest('[data-component]') !== null),
    })),
    completion: [...lab.querySelectorAll('[data-completion-field]')].map(n => ({key: n.dataset.completionField,
      value: n.querySelector('dd').textContent})),
    height: lab.querySelector('.walkthrough-progress').getBoundingClientRect().height};
}
function check(result, tag) {
  assert(result.page <= result.width + 1, `${tag}: page overflow`);
  assert(result.lab <= result.client + 1, `${tag}: lab overflow`);
  assert(result.visual <= result.visualClient + 1, `${tag}: diagram overflow`);
  assert.deepEqual(result.clipped, [], `${tag}: text is wider than its own component`);
  assert.equal(result.explanationLinks, 0, `${tag}: duplicate source links in explanation`);
  if (result.view === 'overview') {
    assert.equal(result.components.length, 1, `${tag}: one shared component layout`);
    const {columns, misplaced} = result.components[0];
    assert.deepEqual(columns.map(c => c.id), ['guest', 'kvm', 'qemu', 'host-kernel', 'hardware'],
      `${tag}: stable component order, including inactive/background components`);
    assert.deepEqual(columns.map(c => c.title), ['Guest', 'KVM', 'QEMU', 'Host kernel', '物理硬件'],
      `${tag}: identical component headings across every article`);
    assert(!misplaced, `${tag}: independent backends must not become QEMU children`);
    if (result.width >= 1100) for (const [index, column] of columns.entries()) {
      assert(Math.abs(column.width - columns[0].width) < 1, `${tag}: equal component columns`);
      assert(Math.abs(column.titleTop - columns[0].titleTop) < 1, `${tag}: aligned headings`);
      assert(Math.abs(column.bodyTop - columns[0].bodyTop) < 1, `${tag}: aligned component bodies`);
      if (index) assert(column.left > columns[index - 1].left, `${tag}: spatial reading order`);
    }
  }
  if (result.snapshot?.lane) {
    assert.equal(result.activeLane, result.snapshot.lane, `${tag}: CPU execution location`);
    assert.equal(result.currentLane, result.snapshot.lane, `${tag}: accessible current CPU context`);
    assert.equal(result.inactiveStatuses, 0, `${tag}: inactive CPU contexts do not repeat empty-state text`);
    for (const field of result.cpu) assert.equal(field.now, result.snapshot.fields[field.key]);
  }
  for (const item of result.completion) assert.equal(item.value, result.snapshot.fields[item.key], `${tag}: independent completion state`);
}

(async () => {
  const browser = await launchBrowser({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let layouts = 0, scenarios = 0;
  try {
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: width > 700 ? 900 : 844});
      for (const slug of pages) {
        await page.goto(`${base}/mechanisms/${slug}.html`);
        const disk = slug === 'virtio-blk-read', prefix = disk ? 'vbr' : 'execution';
        await page.locator(`[data-${prefix}-next]`).waitFor({state: 'visible'});
        const variants = await page.evaluate(disk => disk
          ? JSON.parse(document.querySelector('#block-io-scenarios-data').textContent).routes
            .flatMap(r => ['read', 'write'].map(operation => ({backend: r.id, operation})))
          : JSON.parse(document.querySelector('#execution-demo-data').textContent).scenarios.map(s => ({scenario: s.id})), disk);
        if (disk) variants.push({backend: 'local-blk', operation: 'write', completion: 'flush'},
          {backend: 'local-scsi', operation: 'write', completion: 'fua'});
        for (const variant of variants) {
          const tag = `${slug}/${Object.values(variant).join('/')}/${width}`;
          await page.goto(`${base}/mechanisms/${slug}.html?${new URLSearchParams(variant)}`);
          await page.locator(`[data-${prefix}-next]`).waitFor({state: 'visible'});
          scenarios++;
          for (const view of ['overview', disk ? 'mechanism' : 'structure', 'source']) {
            await page.locator(`[data-${prefix}-view="${view}"]`).click();
            await page.locator(`[data-${prefix}-reset]`).click();
            check(await page.evaluate(inspect), `${tag}/${view}/initial`); layouts++;
            if (slug === 'memory-access' && variant.scenario === 'mmio-read' && view === 'overview') {
              await page.locator('.execution-canvas').screenshot({path: path.join(shots, `memory-mmio-${width}-initial.png`)});
            }
            // Every intermediate frame participates in the overflow audit, not
            // just the empty initial diagram. One round trip per scene/view.
            const results = await page.evaluate(({prefix, code}) => {
              const read = (0, eval)(`(${code})`), next = document.querySelector(`[data-${prefix}-next]`);
              const results = [];
              for (let guard = 0; !next.disabled && guard < 150; guard++) {
                next.click(); results.push(read());
              }
              if (!next.disabled) throw new Error('Unfinished playback');
              return results;
            }, {prefix, code: inspect.toString()});
            results.forEach((r, i) => check(r, `${tag}/${view}/${i}`)); layouts += results.length;
          }
          if (variant === variants[0]) {
            await page.locator(`[data-${prefix}-view="overview"]`).click();
            await page.locator('[data-walkthrough-expand]').first().click();
            await page.waitForFunction(() => !!document.querySelector('.walkthrough-expanded'));
            check(await page.evaluate(inspect), `${tag}/fullscreen`);
            assert.equal(await page.locator('[data-walkthrough-expand]').first().textContent(), '退出全屏');
            await page.screenshot({path: path.join(shots, `${slug}-${width}-fullscreen.png`)});
            if (width < 700) {
              await page.locator('[data-walkthrough-lab]').first().evaluate(lab => { lab.scrollTop = lab.scrollHeight; });
              const exit = await page.locator('[data-walkthrough-expand]').first().boundingBox();
              assert(exit.y >= 0 && exit.y + exit.height <= 844, `${tag}: fullscreen exit remains reachable after scrolling`);
            }
            await page.locator('[data-walkthrough-expand]').first().click();
          }
        }
        console.log(`PASS ${slug} ${width}px: ${variants.length} scenarios, every frame in all three views`);
      }
    }
    assert.deepEqual(errors, []);
    console.log(`PASS ${scenarios} scenario/viewport combinations, ${layouts} frame layouts; screenshots: ${shots}`);
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
