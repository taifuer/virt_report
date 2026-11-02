/* Run against the local static export; never fetches upstream data or calls a model. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const model = require('../virt_report/render/assets/execution-model.js');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const routes = {
  'network-io': 'network-io', 'memory-access': 'memory-access',
  'cpu-execution': 'cpu-execution', 'interrupt-delivery': 'interrupt-delivery',
  'gpu-execution': 'gpu-execution', 'vm-lifecycle': 'libvirt-domain-start',
  'virtio-gpu-rendering': 'virtio-gpu-rendering',
};
const ids = process.env.EXECUTION_DEMO_IDS?.split(',') || Object.keys(routes);
assert(ids.length && ids.every(id => Object.hasOwn(routes, id)), 'known demo IDs are required');
const queryKey = (_id, key) => key;
const shots = process.env.EXECUTION_SCREENSHOT_DIR;
if (shots) fs.mkdirSync(shots, {recursive: true});
const button = (page, name) => page.locator(`[data-execution-${name}]`);

function selectorMembership(demo, scenarioId) {
  const categories = demo.selector.categories || [{id: '', groups: demo.selector.groups}];
  for (const category of categories) for (const group of category.groups) {
    if (group.scenarios.some(option => option.id === scenarioId)) return {category, group};
  }
  throw new Error(`Scenario is absent from selector metadata: ${demo.id}/${scenarioId}`);
}

async function assertSelectorState(page, demo, scenarioId) {
  const {category, group} = selectorMembership(demo, scenarioId);
  assert.equal(await page.locator('[data-execution-lab]').getAttribute('data-scenario'), scenarioId);
  if (demo.selector.categories) {
    assert(await button(page, 'category').isVisible());
    assert.equal(await button(page, 'category').inputValue(), category.id);
    assert.deepEqual(await button(page, 'category').locator('option').evaluateAll(options => options.map(o => o.value)),
      demo.selector.categories.map(item => item.id));
  } else {
    assert.equal(await button(page, 'category').count(), 0);
  }
  const operations = demo.selector.operations || [];
  assert.deepEqual(await page.locator('[data-execution-operation]').evaluateAll(nodes => nodes.map(n => n.dataset.executionOperation)),
    operations.map(operation => operation.id));
  for (const operation of operations) {
    const control = page.locator(`[data-execution-operation="${operation.id}"]`);
    assert(await control.isVisible());
    assert.equal(await control.getAttribute('aria-pressed'), String(operation.id === group.operation));
  }
  const groups = category.groups.filter(item => !operations.length || item.operation === group.operation);
  const options = groups.flatMap(item => item.scenarios);
  const scenario = button(page, 'scenario');
  assert.equal(await scenario.inputValue(), scenarioId);
  assert.deepEqual(await scenario.locator('option').evaluateAll(nodes => nodes.map(node => ({id: node.value, label: node.textContent}))), options);
  assert.deepEqual(await scenario.locator('optgroup').evaluateAll(nodes => nodes.map(node => node.label)),
    groups.length > 1 ? groups.map(item => item.label) : []);
  const hidden = Boolean(demo.selector.categories && options.length === 1);
  assert.equal(await button(page, 'scenario-choice').isHidden(), hidden,
    `${demo.id}/${scenarioId}: only meaningful condition selectors are visible`);
}

async function chooseScenario(page, demo, scenarioId) {
  const {category, group} = selectorMembership(demo, scenarioId);
  if (demo.selector.categories && await button(page, 'category').inputValue() !== category.id) {
    await button(page, 'category').selectOption(category.id);
  }
  if (demo.selector.operations) {
    const direction = page.locator(`[data-execution-operation="${group.operation}"]`);
    if (await direction.getAttribute('aria-pressed') !== 'true') await direction.click();
  }
  if (await button(page, 'scenario').inputValue() !== scenarioId) {
    assert(await button(page, 'scenario').isVisible(), `${scenarioId}: preset must be selectable`);
    await button(page, 'scenario').selectOption(scenarioId);
  }
  await assertSelectorState(page, demo, scenarioId);
}

async function assertSelectorTransitions(page, demo) {
  if (demo.id === 'network-io') {
    for (const operation of ['tx', 'rx']) {
      const exceptional = operation === 'tx' ? 'qemu-tx-backpressure' : 'qemu-rx-no-buffer';
      await chooseScenario(page, demo, exceptional);
      // Every other backend keeps the chosen direction and uses its normal preset.
      for (const category of demo.selector.categories.filter(item => item.id !== 'qemu')) {
        await button(page, 'next').click();
        assert(await button(page, 'prev').isEnabled());
        await button(page, 'category').selectOption(category.id);
        const normal = category.groups.find(group => group.operation === operation).scenarios[0].id;
        await assertSelectorState(page, demo, normal);
        assert(await button(page, 'prev').isDisabled(), 'backend changes reset progress');
      }
      await button(page, 'category').selectOption('qemu');
      await assertSelectorState(page, demo, `qemu-${operation}`);
      await chooseScenario(page, demo, exceptional);
      const opposite = operation === 'tx' ? 'rx' : 'tx';
      await page.locator(`[data-execution-operation="${opposite}"]`).click();
      await assertSelectorState(page, demo, `qemu-${opposite}`);
    }
  } else if (demo.id === 'memory-access') {
    await chooseScenario(page, demo, 'dirty-write-protect');
    await button(page, 'category').selectOption('mmio');
    await assertSelectorState(page, demo, 'mmio-read');
    await button(page, 'category').selectOption('ram');
    await assertSelectorState(page, demo, 'ram-hit');
  }
}

async function assertNetworkQueuePlayback(page, demo) {
  const scene = demo.scenarios.find(item => item.id === 'qemu-rx');
  const trace = model.buildTrace(scene), stops = model.stops(trace, 'structure');
  await chooseScenario(page, demo, scene.id);
  await page.locator('[data-execution-view="structure"]').click();
  await page.locator('[data-execution-step]').nth(stops.length - 2).click();
  await button(page, 'speed').selectOption('1200');
  await button(page, 'play').click();
  await page.waitForFunction(() => document.querySelector('[data-execution-next]').disabled
    && document.querySelector('[data-execution-play]').getAttribute('aria-pressed') === 'false');
  await assertFrame(page, trace[stops.at(-1)]);
  assert.equal(trace[stops.at(-1)].id, 'refill', 'queue playback ends when RX buffers are replenished');
  assert.equal(new URL(page.url()).searchParams.get('step'), 'refill');
  // Replaying from the view-specific final step restarts at its initial state.
  await button(page, 'play').click();
  await assertFrame(page, trace[0]);
  await button(page, 'play').click();
}

async function assertFrame(page, frame) {
  assert.equal(await page.locator('.walkthrough-inspector a').count(), 0,
    'sources belong exclusively in the source view');
  const highlighted = await page.locator('[data-execution-sources] [data-source-id][data-current="true"]')
    .evaluateAll(nodes => [...new Set(nodes.map(node => node.dataset.sourceId))].sort());
  assert.deepEqual(highlighted, [...new Set(frame.sourceIds)].sort(),
    `${frame.id}: every source is available and highlighted in the source view`);
  const state = await page.locator('[data-execution-lab]').evaluate(lab => ({
    step: lab.dataset.step,
    sourceExpanded: lab.dataset.view !== 'source' || [...lab.querySelectorAll('[data-execution-sources]')]
      .every(node => node.scrollHeight <= node.clientHeight + 1),
    fields: Object.fromEntries([...lab.querySelectorAll('[data-execution-field]')].map(node =>
      [node.dataset.executionField, node.querySelector('dd').textContent])),
    hiddenFields: lab.executionSnapshot.scene.structures.flatMap(group =>
      group.fields.filter(field => field.hidden).map(field => field.key)),
    changed: [...lab.querySelectorAll('[data-execution-field][data-current="true"]')].map(node => node.dataset.executionField),
    active: [...lab.querySelectorAll('[data-execution-node][data-active="true"]')].map(node => node.dataset.executionNode),
    nodes: Object.fromEntries([...lab.querySelectorAll('[data-execution-node]')].map(node =>
      [node.dataset.executionNode, node.querySelector('.execution-node-status').textContent])),
  }));
  assert.equal(state.step, frame.id);
  assert(state.sourceExpanded, `${frame.id}: source paths must expand with the page, without an internal scroller`);
  const expectedFields = Object.fromEntries(Object.entries(frame.values).filter(([key]) =>
    !state.hiddenFields.includes(key) && (!frame.queueState || key in state.fields)));
  assert.deepEqual(state.fields, expectedFields);
  assert.deepEqual(state.changed.sort(), frame.changed.filter(key => key in expectedFields).sort());
  assert.deepEqual(state.active.sort(), [...frame.active].sort());
  assert.deepEqual(state.nodes, frame.nodes);
  assert.equal(await button(page, 'title').textContent(), frame.title);
  if (frame.queueState) {
    const sceneLabel = await page.locator('[data-execution-lab]').evaluate(lab =>
      `${lab.getAttribute('aria-label')}/${lab.dataset.scenario}/${lab.dataset.view}/${lab.dataset.step}`);
    assert.equal(await page.locator('[data-execution-structures] [data-typed-queue]').count(), 1,
      `${sceneLabel}: structure view must contain exactly one full typed queue`);
    const actual = await page.locator('[data-execution-structures] [data-typed-queue]').evaluate(queue => ({
      rows:[...queue.querySelectorAll('tbody tr')].map(row=>({index:Number(row.dataset.descriptor),state:row.dataset.state,text:row.textContent})),
      rings:Object.fromEntries([...queue.querySelectorAll('[data-queue-ring]')].map(ring=>[ring.dataset.queueRing, [...ring.querySelectorAll('.walkthrough-queue-slots > div')].map(slot=>slot.querySelector('span').textContent)])),
    }));
    if (frame.queueState.descriptors.length) {
      assert.deepEqual(actual.rows.map(row=>row.index),frame.queueState.descriptors.map(desc=>desc.index));
      for (const descriptor of frame.queueState.descriptors) {
        const row=actual.rows.find(row=>row.index===descriptor.index);
        assert(row.text.includes(`0x${descriptor.addr.toString(16)}`));
        assert(row.text.includes(String(descriptor.len)));
      }
    } else assert(actual.rows.every(row=>row.state==='unprepared'));
    for (const name of ['avail','used']) assert.deepEqual(actual.rings[name],frame.queueState[name].slots.map(record=>
      record===null?'—':name==='avail'?`头 ${record}`:`id ${record.id}`));
  } else if (Object.hasOwn(frame.values, 'availHead')) {
    for (const name of ['avail', 'used']) {
      const record = frame.values[name === 'avail' ? 'availHead' : 'usedEntry'];
      const slot = record.match(/slot\s+(\d+)/)?.[1];
      const ring = page.locator(`[data-execution-ring="${name}"]`);
      if (slot !== undefined) {
        assert.equal(await ring.locator('.execution-ring-slots > div').nth(Number(slot) % 8).getAttribute('title'), record);
      } else {
        assert.equal(await ring.locator('[data-filled="true"]').count(), 0);
      }
      assert.equal(await ring.locator('.execution-ring-record').textContent(), record === '空' ? '尚无记录' : record);
    }
  }
}

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const unexpected = [];
  page.on('request', request => { if (!request.url().startsWith(base)) unexpected.push(request.url()); });
  let scenarioCount = 0, checkedSteps = 0;
  try {
    for (const id of ids) {
      const route = `${base}/mechanisms/${routes[id]}.html`;
      await page.goto(route);
      assert(await button(page, 'controls').isVisible(), `${id}: controls`);
      assert(await button(page, 'fallback').isHidden());
      const demo = JSON.parse(await page.locator('#execution-demo-data').textContent());
      await assertSelectorState(page, demo, demo.defaultScenario);
      for (const scene of demo.scenarios) {
        const trace = model.buildTrace(scene);
        await chooseScenario(page, demo, scene.id);
        for (const view of ['overview', 'structure', 'source']) {
          await page.locator(`[data-execution-view="${view}"]`).click();
          await button(page, 'reset').click();
          await assertFrame(page, trace[0]);
          assert(await button(page, 'prev').isDisabled());
          const stops = model.stops(trace, view);
          if (id === 'network-io' && view === 'structure') {
            const direction = selectorMembership(demo, scene.id).group.operation;
            assert.equal(trace[stops.at(-1)].id, direction === 'rx' ? 'refill' : 'reclaim');
            assert(stops.every(stop => trace[stop].focus), `${scene.id}: queue view has queue-related steps only`);
          }
          assert.deepEqual(await page.locator('[data-execution-step] > span').allTextContents(),
            model.navigation(scene, trace, view).map((entry, n, entries) => {
              if (entry.branch) {
                const letter = String.fromCharCode(65 + scene.parallel.branches.findIndex(branch => branch.id === entry.branch));
                return letter + String(entries.filter(item => item.branch === entry.branch).indexOf(entry) + 1).padStart(2, '0');
              }
              return view === 'structure' && n === 0 ? '○' : String(n + (view === 'structure' ? 0 : 1)).padStart(2, '0');
            }));
          assert.deepEqual(await page.locator('[data-execution-step] > strong').allTextContents(),
            model.navigation(scene, trace, view).map(entry => entry.title));
          for (const [position, stop] of stops.entries()) {
            await assertFrame(page, trace[stop]); checkedSteps++;
            if (position < stops.length - 1) await button(page, 'next').click();
          }
          assert(await button(page, 'next').isDisabled());
          await button(page, 'prev').click();
          await assertFrame(page, trace[stops.at(-2)]);
          await page.reload();
          await assertFrame(page, trace[stops.at(-2)]);
          await assertSelectorState(page, demo, scene.id);
          assert.equal(await page.locator('[data-execution-lab]').getAttribute('data-view'), view);
        }
        scenarioCount++;
      }
      await assertSelectorTransitions(page, demo);
      if (id === 'network-io') await assertNetworkQueuePlayback(page, demo);
      // Deep links and unknown parameters have safe fallbacks.
      const invalidQuery = new URLSearchParams(['scenario', 'view', 'step'].map(key => [queryKey(id, key), 'unknown']));
      await page.goto(`${route}?${invalidQuery}`);
      assert.equal(await page.locator('[data-execution-lab]').getAttribute('data-scenario'), demo.defaultScenario);
      assert.equal(await page.locator('[data-execution-lab]').getAttribute('data-view'), 'overview');
      await assertSelectorState(page, demo, demo.defaultScenario);
      await button(page, 'speed').selectOption('1200');
      await button(page, 'play').click();
      await page.waitForFunction(() => document.querySelector('[data-execution-prev]').disabled === false);
      await button(page, 'play').click();
      assert.equal(await button(page, 'play').getAttribute('aria-pressed'), 'false');
      await button(page, 'reset').click();
      await page.locator('.execution-canvas').focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(new URL(page.url()).searchParams.get(queryKey(id, 'step')), demo.scenarios.find(s => s.id === demo.defaultScenario).steps[1].id);
      await button(page, 'expand').click();
      await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'true');
      assert.equal(await button(page, 'expand').getAttribute('aria-pressed'), 'true');
      await button(page, 'expand').click();
      await page.waitForFunction(() => document.querySelector('[data-execution-expand]').getAttribute('aria-pressed') === 'false');
      assert.equal(await button(page, 'expand').getAttribute('aria-pressed'), 'false');
      for (const width of [1440, 1024, 768, 390, 320]) {
        await page.setViewportSize({width, height: 1000});
        for (const view of ['overview', 'structure', 'source']) {
          await page.locator(`[data-execution-view="${view}"]`).click();
          const size = await page.evaluate(() => ({width: innerWidth, actual: document.documentElement.scrollWidth}));
          assert(size.actual <= size.width + 1, `${id}/${view}: ${JSON.stringify(size)}`);
        }
        await page.locator('[data-execution-view="structure"]').click();
        if (shots && [1440, 390].includes(width)) {
          await button(page, 'controls').scrollIntoViewIfNeeded();
          await page.screenshot({path: path.join(shots, `${id}-${width}.png`)});
        }
      }
      await page.setViewportSize({width: 1440, height: 1000});
      console.log(`PASS ${id}: ${demo.scenarios.length} scenarios, selector groups/directions, all steps/views and 5 widths`);
    }
    const nojs = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 844}});
    const reader = await nojs.newPage();
    for (const id of ids) {
      await reader.goto(`${base}/mechanisms/${routes[id]}.html`);
      assert(await button(reader, 'controls').isHidden());
      assert(await button(reader, 'fallback').isVisible());
      assert(await button(reader, 'fallback').locator('details[open] li').count() > 2);
    }
    await nojs.close();
    const expectedScenarios = ids.reduce((count, id) => count + JSON.parse(fs.readFileSync(
      path.join(__dirname, `../virt_report/content/flows/${id}.json`), 'utf8')).scenarios.length, 0);
    assert.equal(scenarioCount, expectedScenarios, 'all curated scenarios were exercised');
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log(`PASS ${scenarioCount} scenarios; ${checkedSteps} snapshots; offline/no-JS fallback; no console errors`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
