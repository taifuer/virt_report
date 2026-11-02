/* Local-only regression: independent completion and forwarding branches. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const model = require('../virt_report/render/assets/execution-model.js');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const origin = new URL(base).origin;
assert(['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname), 'use a local preview');
const shots = process.env.PARALLEL_WALKTHROUGH_SCREENSHOT_DIR || '/tmp/parallel-walkthrough-review';
fs.mkdirSync(shots, {recursive: true});
const expectedScenes = ['dpdk-rx', 'dpdk-tx', 'qemu-tx', 'qemu-tx-backpressure', 'vhost-tx'];
const views = ['overview', 'structure', 'source'];

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

function assertCausality(scene, trace, firstBranch) {
  const tag = `${scene.id}/${firstBranch}`;
  const fork = scene.steps.findIndex(step => step.id === scene.parallel.after);
  const ordered = [scene.parallel.branches.find(branch => branch.id === firstBranch),
    ...scene.parallel.branches.filter(branch => branch.id !== firstBranch)];
  const ids = trace.map(frame => frame.id);
  assert.deepEqual(ids, [...scene.steps.slice(0, fork + 1).map(step => step.id),
    ...ordered.flatMap(branch => branch.steps)], `${tag}: only independent suffixes may reorder`);
  assert.equal(new Set(ids).size, scene.steps.length, `${tag}: no skipped or repeated operation`);
  const at = id => trace.find(frame => frame.id === id);
  const before = (a, b) => assert(ids.indexOf(a) < ids.indexOf(b), `${tag}: ${a} precedes ${b}`);
  const forkState = at(scene.parallel.after);
  if (['qemu-tx', 'qemu-tx-backpressure', 'vhost-tx'].includes(scene.id)) {
    const sent = at('egress'), reclaimed = at('reclaim');
    before(scene.parallel.after, 'egress');
    before('fill-used', 'used'); before('used', 'reclaim');
    if (firstBranch === 'host-forwarding') {
      assert.equal(sent.queueState.used.index, 0, `${tag}: local forwarding does not publish used`);
      assert.equal(sent.queueState.used.consumer, 0, `${tag}: local forwarding does not reclaim`);
      assert.equal(sent.queueState.freeDescriptors, 6, `${tag}: guest descriptors remain occupied`);
      assert.equal(sent.values.usedIdx, forkState.values.usedIdx);
      assert.equal(sent.values.reclaimedIdx, forkState.values.reclaimedIdx);
    } else {
      assert.equal(reclaimed.queueState.used.consumer, 1);
      assert.equal(reclaimed.values.networkStage, forkState.values.networkStage,
        `${tag}: guest reclaim cannot import future host forwarding`);
    }
  }
  if (scene.id === 'dpdk-tx') {
    before('route-interrupt', 'pmd'); before('used', 'pmd'); before('pmd', 'pmd-cleanup');
    assert.match(forkState.values.backendState, /dequeue.*返回.*1/, `${tag}: burst has returned before PMD handoff`);
    assert.match(forkState.values.mbufOwner, /应用持有/, `${tag}: application now owns the copied mbuf`);
    if (firstBranch === 'host-forwarding') {
      before('pmd-cleanup', 'reclaim');
      assert.equal(at('pmd').queueState.used.consumer, 0, `${tag}: PMD acceptance is independent of guest reclaim`);
      assert.equal(at('pmd-cleanup').queueState.used.consumer, 0);
      assert.equal(at('pmd-cleanup').values.reclaimedIdx, forkState.values.reclaimedIdx);
    } else {
      assert.equal(at('reclaim').values.pmdQueue, forkState.values.pmdQueue,
        `${tag}: guest reclaim cannot submit a host mbuf to PMD`);
    }
  }
  if (scene.id === 'dpdk-rx') {
    before('route-interrupt', 'free-mbuf'); before('used', 'free-mbuf');
    assert.match(forkState.values.backendState, /enqueue.*返回.*1/, `${tag}: copied enqueue has returned`);
    if (firstBranch === 'host-buffer') {
      before('free-mbuf', 'recvfrom-return');
      assert.equal(at('free-mbuf').queueState.used.consumer, 0, `${tag}: freeing host mbuf needs no guest queue consumption`);
      assert.equal(at('free-mbuf').values.applicationResult, forkState.values.applicationResult);
      assert.equal(at('free-mbuf').values.userBuffer, forkState.values.userBuffer);
    } else {
      before('recvfrom-return', 'free-mbuf');
      assert.equal(at('recvfrom-return').values.mbufOwner, forkState.values.mbufOwner,
        `${tag}: recvfrom does not free the independent host mbuf`);
      assert.equal(at('free-mbuf').queueState.used.consumer, 1,
        `${tag}: host cleanup must not roll back guest queue consumption`);
      assert.equal(at('free-mbuf').queueState.avail.index, at('recvfrom-return').queueState.avail.index,
        `${tag}: host cleanup must preserve guest refill`);
    }
  }
}

async function assertLayout(page, tag, fullscreen) {
  await settle(page);
  const result = await page.locator('[data-execution-lab]').evaluate(lab => {
    const rect = node => { const r = node.getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height}; };
    const nav = lab.querySelector('[data-execution-progress]');
    const bounded = [lab, nav, ...lab.querySelectorAll('.walkthrough-branch, .walkthrough-branch-phases')];
    const canvas = lab.querySelector('.execution-canvas');
    return {viewport: innerWidth, height: innerHeight, pageWidth: document.documentElement.scrollWidth,
      expanded: lab.classList.contains('walkthrough-expanded'), canvas: rect(canvas),
      canvasFits: canvas.scrollWidth <= canvas.clientWidth + 1,
      containers: bounded.map(node => ({name: node.className, scroll: node.scrollWidth, client: node.clientWidth})),
      buttons: [...nav.querySelectorAll('button')].map(button => ({...rect(button),
        parent: rect(button.parentElement), text: button.textContent})),
      branchCount: nav.querySelectorAll('[data-execution-branch]').length,
      view: lab.executionSnapshot.view};
  });
  assert.equal(result.expanded, fullscreen, `${tag}: fullscreen state`);
  assert(result.pageWidth <= result.viewport + 1, `${tag}: page overflow`);
  assert(result.canvasFits, `${tag}: canvas horizontal overflow`);
  for (const item of result.containers)
    assert(item.scroll <= item.client + 1, `${tag}: ${item.name} overflows horizontally`);
  for (const box of result.buttons)
    assert(box.left >= box.parent.left - 1 && box.right <= box.parent.right + 1,
      `${tag}: branch control escapes its wrapped group: ${box.text}`);
  assert.equal(result.branchCount, result.view === 'structure' ? 0 : 2, `${tag}: branch grouping`);
  if (fullscreen && result.viewport >= 1100) {
    assert(result.canvas.height >= 180 && result.canvas.bottom <= result.height + 2,
      `${tag}: expanded diagram retains usable viewport height ${JSON.stringify(result.canvas)}`);
  }
}

async function openScene(page, sceneId, firstBranch, step, view = 'overview') {
  const query = new URLSearchParams({scenario: sceneId, view});
  if (firstBranch) query.set('order', firstBranch);
  if (step) query.set('step', step);
  await page.goto(`${base}/mechanisms/network-io.html?${query}`);
  await page.waitForFunction(id => document.querySelector('[data-execution-lab]')?.executionSnapshot?.scene.id === id, sceneId);
}

async function seek(page, index) {
  await page.locator('[data-execution-lab]').evaluate((lab, target) => {
    for (let guard = 0; lab.executionSnapshot.frame.index !== target && guard < 150; guard++) {
      const direction = lab.executionSnapshot.frame.index < target ? 'next' : 'prev';
      lab.querySelector(`[data-execution-${direction}]`).click();
    }
    if (lab.executionSnapshot.frame.index !== target) throw new Error(`could not seek to ${target}`);
  }, index);
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let checked = 0, layouts = 0;
  try {
    await openScene(page, 'qemu-tx');
    const demo = JSON.parse(await page.locator('#execution-demo-data').textContent());
    const scenes = demo.scenarios.filter(scene => scene.parallel);
    assert.deepEqual(scenes.map(scene => scene.id).sort(), expectedScenes,
      'export the reviewed five parallel network scenarios before running this check');
    for (const viewport of [{width: 1366, height: 900}, {width: 390, height: 844}, {width: 320, height: 740}]) {
      await page.setViewportSize(viewport);
      for (const scene of scenes) for (const branch of scene.parallel.branches) {
        const tag = `${scene.id}/${branch.id}/${viewport.width}`;
        const trace = model.buildTrace(scene, branch.id);
        assertCausality(scene, trace, branch.id);
        const referenceEnd = model.buildTrace(scene, scene.parallel.branches[0].id).at(-1);
        assert.deepEqual(trace.at(-1).values, referenceEnd.values, `${tag}: independent histories converge`);
        const queueEnd = frame => Object.fromEntries(Object.entries(frame.queueState).filter(([key]) => key !== 'changed'));
        assert.deepEqual(queueEnd(trace.at(-1)), queueEnd(referenceEnd), `${tag}: final queue state is order independent`);
        await openScene(page, scene.id);
        await page.locator(`[data-execution-order="${branch.id}"]`).click();
        const fork = trace.find(frame => frame.id === scene.parallel.after);
        assert.deepEqual(await page.locator('[data-execution-lab]').evaluate(lab => ({
          step: lab.executionSnapshot.frame.id, order: lab.executionSnapshot.firstBranch,
          urlOrder: new URL(location.href).searchParams.get('order')})),
        {step: fork.id, order: branch.id, urlOrder: branch.id}, `${tag}: order selection restarts at the fork`);
        for (const view of views) {
          await page.locator(`[data-execution-view="${view}"]`).click();
          const count = await page.locator('[data-execution-lab]').evaluate((lab, {trace, view, order}) => {
            const one = selector => lab.querySelector(selector), scene = lab.executionSnapshot.scene;
            const fail = text => { throw new Error(`${scene.id}/${order}/${view}: ${text}`); };
            const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
            const check = expected => {
              const actual = lab.executionSnapshot;
              if (actual.firstBranch !== order || actual.frame.id !== expected.id || actual.frame.index !== expected.index)
                fail(`wrong frame at ${expected.id}`);
              for (const key of ['values', 'nodes', 'queueState'])
                if (!same(actual.frame[key], expected[key])) fail(`${expected.id}: stale ${key}`);
              const current = [...lab.querySelectorAll('[data-execution-step][aria-current="step"]')];
              if (current.length !== 1 || Number(current[0].dataset.executionStep) > expected.index
                  || Number(current[0].dataset.executionEnd) < expected.index) fail(`${expected.id}: current phase range`);
              const q = expected.queueState;
              for (const [part, name, consumer] of [['descriptors', 'avail', '设备消费'], ['completion', 'used', '驱动回收']]) {
                const text = one(`[data-network-memory-part="${part}"] .network-memory-index`).textContent;
                if (text !== `${name}.idx ${q[name].index} · ${consumer} ${q[name].consumer}`)
                  fail(`${expected.id}: rendered ${name} index disagrees with snapshot`);
              }
              if (one('[data-execution-title]').textContent !== expected.title) fail(`${expected.id}: stale explanation`);
            };
            const nav = ExecutionDemoModel.navigation(scene, trace, view);
            for (const button of lab.querySelectorAll('[data-execution-step]')) {
              const entry = nav.find(item => item.index === Number(button.dataset.executionStep));
              if (!entry || button.querySelector('strong').textContent !== entry.title) fail('navigation title/start mismatch');
              const owner = button.closest('[data-execution-branch]');
              if (view !== 'structure' && (owner?.dataset.executionBranch || undefined) !== entry.branch)
                fail('phase assigned to the wrong branch');
              button.click(); check(trace[entry.index]);
            }
            if (view !== 'structure') {
              scene.parallel.branches.forEach((branch, ordinal) => {
                const group = one(`[data-execution-branch="${branch.id}"]`);
                if (group.querySelector('[data-execution-order]').getAttribute('aria-pressed') !== String(order === branch.id))
                  fail('order selection is not exposed accessibly');
                [...group.querySelectorAll('[data-execution-step]')].forEach((button, n) => {
                  const label = `${String.fromCharCode(65 + ordinal)}${String(n + 1).padStart(2, '0')}`;
                  if (button.querySelector('span').textContent !== label) fail('branches must have independent phase numbering');
                });
              });
            }
            one('[data-execution-reset]').click();
            const stops = ExecutionDemoModel.stops(trace, view);
            let count = 0;
            for (const [position, index] of stops.entries()) {
              if (position) one('[data-execution-next]').click();
              check(trace[index]); count++;
            }
            if (!one('[data-execution-next]').disabled) fail('last visible step does not stop');
            for (const index of stops.slice(0, -1).reverse()) {
              one('[data-execution-prev]').click(); check(trace[index]); count++;
            }
            if (!one('[data-execution-prev]').disabled) fail('first visible step permits backward movement');
            one('[data-execution-reset]').click(); check(trace[0]);
            return count;
          }, {trace, view, order: branch.id});
          checked += count;
          await assertLayout(page, `${tag}/${view}/inline`, false); layouts++;
          await page.locator('[data-walkthrough-expand]').first().click();
          await assertLayout(page, `${tag}/${view}/fullscreen`, true); layouts++;
          await page.locator('[data-walkthrough-expand]').first().click();
        }
        // Projection is checked at a nonfocus step, not just a common initial state.
        const projectionFrame = trace.find(frame => !frame.focus && frame.index > fork.index) || fork;
        await page.locator('[data-execution-view="overview"]').click();
        await seek(page, projectionFrame.index);
        await page.locator('[data-execution-view="structure"]').click();
        const projected = model.project(trace, 'structure', projectionFrame.index);
        assert.equal(await page.locator('[data-execution-lab]').evaluate(lab => lab.executionSnapshot.frame.index), projected);
        await page.locator('[data-execution-view="source"]').click();
        assert.equal(await page.locator('[data-execution-lab]').evaluate(lab => lab.executionSnapshot.frame.index), projected);
        // A URL identifies a branch order and operation, independently of its reordered index.
        const targetId = branch.steps.at(-1), target = trace.find(frame => frame.id === targetId);
        await openScene(page, scene.id, branch.id, targetId);
        assert.deepEqual(await page.locator('[data-execution-lab]').evaluate(lab => ({
          order: lab.executionSnapshot.firstBranch, frame: lab.executionSnapshot.frame})),
        {order: branch.id, frame: target}, `${tag}: deeplink reconstructs the selected history`);
        await assertLayout(page, `${tag}/deeplink`, false); layouts++;
        if (viewport.width === 1366 && scene.id === 'qemu-tx') {
          await page.locator('[data-walkthrough-expand]').first().click();
          await page.locator('[data-execution-lab]').evaluate(lab => { lab.scrollTop = 0; });
          await settle(page);
          await page.screenshot({path: path.join(shots, `qemu-tx-${branch.id}-first-1366.png`)});
          await page.locator('[data-walkthrough-expand]').first().click();
        }
        console.log(`PASS ${tag}: both directions, branch navigation, projection, deeplink and all views`);
      }
    }
    // Changing the scenario must discard the previous scenario's branch order.
    await openScene(page, 'qemu-tx', 'host-forwarding', 'egress');
    await page.locator('[data-execution-lab]').evaluate(lab => {
      const select = lab.querySelector('[data-execution-scenario]');
      select.value = 'qemu-tx-backpressure'; select.dispatchEvent(new Event('change', {bubbles: true}));
    });
    assert.deepEqual(await page.locator('[data-execution-lab]').evaluate(lab => ({scene: lab.executionSnapshot.scene.id,
      step: lab.executionSnapshot.frame.id, order: lab.executionSnapshot.firstBranch})),
    {scene: 'qemu-tx-backpressure', step: demo.scenarios.find(scene => scene.id === 'qemu-tx-backpressure').steps[0].id,
      order: demo.scenarios.find(scene => scene.id === 'qemu-tx-backpressure').parallel.branches[0].id});
    assert.deepEqual(errors, [], 'no browser exceptions');
    console.log(`PASS ${checked} forward/reverse snapshots and ${layouts} layouts; screenshots: ${shots}; external requests blocked`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
