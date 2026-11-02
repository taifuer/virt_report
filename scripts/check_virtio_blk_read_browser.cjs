// Optional browser regression. Set PLAYWRIGHT_MODULE to an existing installation.
// Usage: node scripts/check_virtio_blk_read_browser.cjs http://127.0.0.1:8091
// Set VBR_LAYOUT_ONLY=1 for the fast scenario-selector alignment/typography checks.
// Set VBR_PHASES_ONLY=1 for all-backend phase-button wrapping and text-boundary checks.
// Set VBR_SUMMARY_ONLY=1 for single-paragraph summaries and configuration-detail checks.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const model = require('../virt_report/render/assets/virtio-blk-read-model.js');
const baseScene = JSON.parse(fs.readFileSync(path.join(__dirname,
  '../virt_report/content/flows/virtio-blk-read.json'), 'utf8'));
const scene = model.getScenario(baseScene, 'read');
const writeScene = model.getScenario(baseScene, 'write');
const frames = model.buildTrace(scene);
const writeFrames = model.buildTrace(writeScene);
const queueFrames = model.buildQueueTrace(scene);
const queueInitial = model.buildQueueInitial(scene);
const writeQueueFrames = model.buildQueueTrace(writeScene);
const backendIds = ['local-blk', 'local-scsi', 'rbd-blk', 'spdk-blk', 'spdk-scsi'];
const firstFrame = frames[0];
const finalFrame = frames.at(-1);
const dataFrame = frames.find(frame => frame.state.dataReady);
assert(finalFrame.state.userReturned, 'the terminal state includes the application return');
for (const frame of [...frames, ...writeFrames]) {
  if (frame.state.userReturned) {
    assert(['dataReady', 'statusWritten', 'usedPublished', 'reclaimed', 'completed', 'bioCompleted']
      .every(field => frame.state[field]), `${frame.id}: application cannot return before completion`);
  }
}
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const route = `${base}/mechanisms/virtio-blk-read.html`;
const screenshots = process.env.VBR_SCREENSHOT_DIR;

async function assertPhaseWrapping(page) {
  const layout = await page.evaluate(() => {
    const rect = box => ({top: box.top, bottom: box.bottom, left: box.left, right: box.right,
      width: box.width, height: box.height});
    const strip = document.querySelector('[data-vbr-progress]');
    return {
      width: innerWidth, backend: document.querySelector('[data-vbr-lab]').dataset.backend,
      operation: document.querySelector('[data-vbr-lab]').dataset.operation,
      view: document.querySelector('[data-vbr-lab]').dataset.view,
      container: rect(strip.getBoundingClientRect()),
      scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth,
      buttons: [...strip.querySelectorAll('[data-vbr-phase], [data-vbr-queue-step]')].map(button => {
        const box = button.getBoundingClientRect(), style = getComputedStyle(button);
        const size = key => parseFloat(style[key]) || 0;
        return {
          id: button.dataset.vbrPhase || button.dataset.vbrQueueStep, title: button.textContent, box: rect(box),
          content: {left: box.left + size('borderLeftWidth') + size('paddingLeft'),
            right: box.right - size('borderRightWidth') - size('paddingRight'),
            top: box.top + size('borderTopWidth') + size('paddingTop'),
            bottom: box.bottom - size('borderBottomWidth') - size('paddingBottom')},
          text: [...button.querySelectorAll('span, strong')].flatMap(node => {
            const range = document.createRange(); range.selectNodeContents(node);
            return [...range.getClientRects()].filter(item => item.width && item.height).map(rect);
          }),
        };
      }),
    };
  });
  const context = `${layout.width}px ${layout.backend}/${layout.operation}`;
  const inside = (inner, outer) => inner.left >= outer.left - 1 && inner.right <= outer.right + 1
    && inner.top >= outer.top - 1 && inner.bottom <= outer.bottom + 1;
  assert.equal(layout.buttons.length, layout.view === 'mechanism' ? 8 : 10, context);
  assert(layout.scrollWidth <= layout.clientWidth + 1, `${context}: phases must wrap, not scroll horizontally`);
  const rows = new Set(layout.buttons.map(button => Math.round(button.box.top)));
  if (layout.width <= 700) assert(rows.size >= 2, `${context}: phase buttons must wrap onto multiple rows`);
  for (const [index, button] of layout.buttons.entries()) {
    assert(inside(button.box, layout.container), `${context}: button ${button.title} escapes its container`);
    assert(button.box.height >= 44, `${context}: ${button.title} must retain its touch target`);
    assert(button.text.length > 0, `${context}: ${button.title} needs visible text`);
    for (const text of button.text) {
      assert(inside(text, button.content),
        `${context}: text overflows ${button.title}: ${JSON.stringify({text, content: button.content})}`);
    }
    for (const previous of layout.buttons.slice(0, index)) {
      const overlapX = Math.min(button.box.right, previous.box.right) - Math.max(button.box.left, previous.box.left);
      const overlapY = Math.min(button.box.bottom, previous.box.bottom) - Math.max(button.box.top, previous.box.top);
      assert(overlapX <= 1 || overlapY <= 1, `${context}: ${previous.title} overlaps ${button.title}`);
    }
  }
}

async function assertScenarioHeader(page) {
  assert.equal(await page.locator('[data-vbr-lab] > [data-vbr-scenario-summary]').count(), 1,
    'each scenario has one top-level summary paragraph');
  assert.equal(await page.locator('[data-vbr-lab] > [data-vbr-control-plane]').count(), 0,
    'initialization details do not form a second top-level paragraph');
  assert.equal(await page.locator('details[data-vbr-configuration] [data-vbr-control-plane]').count(), 1,
    'control-plane details belong to the scenario configuration');
  const layout = await page.evaluate(() => {
    const rect = node => {
      const {top, bottom, left, right, width, height} = node.getBoundingClientRect();
      return {top, bottom, left, right, width, height};
    };
    const typography = node => {
      const style = getComputedStyle(node);
      return Object.fromEntries(['fontSize', 'fontFamily', 'fontWeight', 'lineHeight', 'color']
        .map(key => [key, style[key]]));
    };
    const select = document.querySelector('[data-vbr-backend-select]');
    const label = [...document.querySelectorAll('label')].find(item => item.control === select);
    return {
      width: innerWidth, page: document.documentElement.scrollWidth,
      group: rect(document.querySelector('[data-vbr-operation-controls]')),
      select: rect(select), label: label && rect(label),
      buttons: [...document.querySelectorAll('[data-vbr-operation]')].map(rect),
      note: rect(document.querySelector('[data-vbr-operation-note]')),
      body: typography(document.querySelector('#preface > p')),
      paragraphs: [...document.querySelectorAll('[data-vbr-lab] > [data-vbr-scenario-summary]')]
        .filter(node => !node.hidden).map(node => ({text: node.textContent, typography: typography(node)})),
    };
  });
  const message = `${layout.width}px scenario header: ${JSON.stringify(layout)}`;
  const same = (a, b) => Math.abs(a - b) <= 1;
  const middle = box => (box.top + box.bottom) / 2;
  assert(layout.page <= layout.width + 1, message);
  assert(layout.label && layout.label.bottom <= layout.select.top + 1, message);
  assert.equal(layout.buttons.length, 2, message);
  for (const box of [layout.select, ...layout.buttons]) {
    assert(same(box.height, 44), message);
    assert(box.left >= layout.group.left - 1 && box.right <= layout.group.right + 1, message);
  }
  assert(same(layout.buttons[0].top, layout.buttons[1].top), message);
  assert(same(layout.buttons[0].bottom, layout.buttons[1].bottom), message);
  if (layout.width >= 701) {
    for (const button of layout.buttons) {
      assert(same(button.top, layout.select.top) && same(button.bottom, layout.select.bottom), message);
    }
    assert(same(middle(layout.note), middle(layout.select)), message);
  } else {
    assert(same(layout.select.left, layout.group.left) && same(layout.select.right, layout.group.right), message);
    assert(layout.select.bottom <= layout.buttons[0].top + 1, message);
    const beside = layout.note.left >= layout.buttons[1].right - 1
      && same(middle(layout.note), middle(layout.buttons[0]));
    const below = layout.note.top >= layout.buttons[0].bottom - 1;
    assert(beside || below, message);
  }
  assert(layout.note.left >= layout.group.left - 1 && layout.note.right <= layout.group.right + 1, message);
  assert.equal(layout.paragraphs.length, 1, message);
  for (const paragraph of layout.paragraphs) {
    assert.deepEqual(paragraph.typography, layout.body,
      `${layout.width}px scenario explanation must use the normal article typography: ${paragraph.text}`);
  }
}

async function assertSingleSummary(page, activeScene) {
  await assertScenarioHeader(page);
  const summary = page.locator('[data-vbr-lab] > [data-vbr-scenario-summary]');
  assert.equal(await summary.textContent(), activeScene.summary, 'summary follows the selected operation and backend');
  if (activeScene.request.backend?.startsWith('spdk-')) {
    assert.match(await summary.textContent(), /QEMU.*控制面/, 'SPDK summary retains the control/data-plane distinction');
  }
  const configuration = page.locator('details[data-vbr-configuration]');
  const control = configuration.locator('[data-vbr-control-plane]');
  const expected = activeScene.presentation?.controlPlane || '';
  assert.equal(await configuration.evaluate(node => node.open), false);
  assert(await control.isHidden(), 'configuration details are not visible above the visualization');
  assert.equal(await control.textContent(), expected, 'switching backends clears or replaces initialization details');
  await configuration.locator('summary').click();
  assert.equal(await configuration.evaluate(node => node.open), true);
  assert.equal(await control.isVisible(), Boolean(expected), 'nonempty initialization details are available on expansion');
  assert.equal(await control.textContent(), expected);
  assert.deepEqual(await configuration.locator('[data-vbr-assumptions] > li').allTextContents(), activeScene.assumptions);
  await configuration.locator('summary').click();
  assert(await control.isHidden());
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
}

async function assertView(page, view) {
  assert.equal(await page.locator('.vbr-visual [data-vbr-event], .vbr-visual [data-vbr-ownership]').count(), 0,
    'step explanations are not repeated below the diagram');
  assert.equal(await page.locator('.vbr-inspector [data-vbr-event]').count(), 1);
  assert.equal(await page.locator('.vbr-inspector [data-vbr-state-note][data-vbr-ownership]').count(), 1);
  if (view !== 'overview') assert(await page.locator('[data-vbr-event]').isHidden(),
    'detailed views do not repeat the step title or edge caption');
  for (const [key, selected] of Object.entries({board: 'overview', structure: 'mechanism', callpaths: 'source'})) {
    assert.equal(await page.locator(`[data-vbr-${key}]`).isVisible(), view === selected,
      `${view}: only its own visualization is visible (${key})`);
  }
  assert.equal(await page.locator('.vbr-visual > [data-vbr-structure]').count(), 1,
    'queue mechanism belongs in the main visualization area');
  assert.equal(await page.locator('[data-vbr-interrupt-path]').isVisible(), view === 'overview',
    'interrupt injection details stay in the overview');
  assert.equal(await page.locator('[data-vbr-queue-step]').count(), view === 'mechanism' ? 8 : 0);
  assert.equal(await page.locator('[data-vbr-phase]').count(), view === 'mechanism' ? 0 : 10);
}

async function assertFrame(page, frame, activeScene) {
  assert.equal(await page.locator('[data-vbr-lab]').getAttribute('data-step'), frame.id);
  await assertTypedQueue(page, frame, activeScene);
  const filled = page.locator('[data-vbr-byte][data-filled="true"]');
  assert.equal(await filled.count(), frame.state.bufferReady ? 16 : 0);
  const view = await page.locator('[data-vbr-lab]').getAttribute('data-view');
  assert.equal(await page.locator('[data-vbr-byte][data-filled="true"]:visible').count(),
    frame.state.bufferReady && view === 'overview' ? 16 : 0);
  assert.equal(await page.locator('[data-vbr-node="application"]').getAttribute('data-done'),
    String(frame.state.userReturned), `${frame.id}: application completion indicator`);
  assert.equal(await page.locator('[data-vbr-traveler]').getAttribute('hidden'), '', 'reduced motion disables travel');
  for (const [stage, key] of Object.entries({signal: 'interruptSignaled', pending: 'irqPending',
    injected: 'irqInjected', handled: 'guestNoticed'})) {
    assert.equal(await page.locator(`[data-vbr-irq-stage="${stage}"]`).getAttribute('data-done'),
      String(frame.state[key]), `${frame.id}: distinct interrupt milestone ${stage}`);
  }
}

async function assertTypedQueue(page, frame, activeScene) {
  const {state, meta} = model.buildQueueSnapshot(activeScene, frame);
  // One browser round trip per frame; still compare every rendered field/slot.
  const containers = await page.locator('[data-vbr-queue-overview], [data-vbr-queue-detail]').evaluateAll(nodes => nodes.map(node => ({
    compact: node.hasAttribute('data-vbr-queue-overview'),
    summaries: Number(node.hasAttribute('data-queue-summary')) + node.querySelectorAll('[data-queue-summary]').length,
    summary: Object.fromEntries([...node.querySelectorAll('[data-queue-summary-field]')]
      .map(field => [field.dataset.queueSummaryField, field.textContent])),
    tables: node.querySelectorAll('table').length,
    headings: [...node.querySelectorAll('thead th')].map(cell => cell.textContent),
    heading: node.querySelector('header span')?.textContent,
    descriptors: [...node.querySelectorAll('[data-descriptor]')].map(row => ({
      index: Number(row.dataset.descriptor), state: row.dataset.state, current: row.dataset.current,
      cells: [...row.querySelectorAll('td')].map(cell => cell.textContent),
    })),
    rings: Object.fromEntries([...node.querySelectorAll('[data-queue-ring]')].map(ring => [ring.dataset.queueRing, {
      text: ring.querySelector(':scope > p').textContent,
      slots: [...ring.querySelectorAll('.walkthrough-queue-slots > div')].map(slot => ({
        filled: slot.dataset.filled, returned: slot.dataset.returned, label: slot.querySelector('span').textContent,
        small: [...slot.querySelectorAll('small')].map(small => small.textContent),
      })),
    }])),
    buffers: [...node.querySelectorAll('[data-buffer]')].map(buffer => ({id:buffer.dataset.buffer,
      label:buffer.querySelector('strong').textContent, text:buffer.querySelector('span').textContent})),
    response: node.querySelector('.walkthrough-queue-response')?.textContent,
  })));
  assert.equal(containers.length, 2);
  for (const container of containers) {
    if (container.compact) {
      assert.equal(container.summaries, 1, 'overview uses one compact Guest RAM summary');
      assert.deepEqual(Object.keys(container.summary), ['chain', 'avail', 'used', 'free']);
      assert.equal(container.summary.chain, state.descriptors.length
        ? state.descriptors.map(descriptor => descriptor.index).join(' → ') : '尚未准备');
      assert.equal(container.summary.avail, `idx ${state.avail.index} · 设备消费 ${state.avail.consumer}`);
      assert.equal(container.summary.used, `idx ${state.used.index} · 驱动回收 ${state.used.consumer}`);
      assert.equal(container.summary.free, `${state.freeDescriptors} / ${meta.size}`);
      assert.equal(container.tables, 0, 'overview does not duplicate the detail descriptor table');
      assert.deepEqual(container.rings, {}, 'overview does not duplicate all ring slots');
      assert.deepEqual(container.buffers, [], 'overview does not duplicate all buffer cards');
      continue;
    }
    assert.equal(container.summaries, 0, 'queue detail retains the full typed structures');
    assert.equal(container.tables, 1);
    assert.deepEqual(container.headings,
      ['描述符 / 缓冲区', 'addr', 'len', 'flags', 'next']);
    assert.match(container.heading,
      new RegExp(`队列 ${meta.index} · ${meta.size} 项 · 空闲描述符 ${state.freeDescriptors}`));
    const descriptors = state.descriptors.length ? state.descriptors
      : meta.descriptorIndices.map(index => ({index,label:'尚未填写',state:'unprepared'}));
    assert.equal(container.descriptors.length, descriptors.length);
    for (const desc of descriptors) {
      const row = container.descriptors.find(row => row.index === desc.index);
      assert.equal(row.state, desc.state);
      assert.equal(row.current, String(state.changed.includes('descriptors')));
      assert.deepEqual(row.cells, [
        `desc[${desc.index}] · ${desc.label}`,
        desc.addr === undefined ? '—' : typeof desc.addr === 'number' ? `0x${desc.addr.toString(16)}` : desc.addr,
        String(desc.len ?? '—'), desc.flags ? (desc.flags.join(' | ') || '设备只读') : '—', String(desc.next ?? '—'),
      ]);
    }
    for (const name of ['avail','used']) {
      const ring = container.rings[name], expected = state[name];
      assert.equal(ring.text,
        `${name}.idx = ${expected.index} · ${name === 'avail' ? '设备消费' : '驱动回收'}位置 = ${expected.consumer}`);
      assert.equal(ring.slots.length, meta.size);
      for (let n = 0; n < meta.size; n++) {
        const slot = ring.slots[n], record = expected.slots[n];
        assert.equal(slot.filled, String(record != null));
        assert.equal(slot.returned, String(record != null && expected.consumer > n));
        assert.equal(slot.label, record == null ? '—'
          : name === 'avail' ? `头 ${record}` : `id ${record.id}`);
        if (name === 'used' && record != null) assert.equal(slot.small.at(-1), `len ${record.len}`);
      }
    }
    assert.equal(container.buffers.length, state.buffers.length);
    for (const buffer of state.buffers) {
      const node = container.buffers.find(node => node.id === buffer.id);
      assert.equal(node.label, buffer.label);
      assert.equal(node.text,
        `${typeof buffer.addr === 'number' ? '0x' + buffer.addr.toString(16) : buffer.addr} · 容量 ${buffer.length} B · 有效 ${buffer.validBytes} B`);
    }
    assert.equal(container.response, !state.response.ready ? '尚未回写响应'
      : state.response.kind === 'scsi' ? 'response OK · status GOOD · resid 0 · sense_len 0' : 'status 0x00 · OK');
  }
}

async function assertQueueFrame(page, frame, activeScene) {
  await assertFrame(page, frame, activeScene);
  await assertView(page, 'mechanism');
  assert.equal(await page.locator('[data-vbr-title]').textContent(), frame.queueTitle);
  assert.equal(await page.locator('[data-vbr-counter]').textContent(),
    frame.queueIndex < 0 ? '准备前 · 共 8 步' : `队列交接 · 第 ${frame.queueIndex + 1} / 8 步`);
  if (frame.queueIndex < 0) {
    assert.equal(await page.locator('[data-vbr-queue-step][aria-current="step"]').count(), 0);
  } else {
    assert.equal(await page.locator('[data-vbr-queue-step][aria-current="step"]')
      .getAttribute('data-vbr-queue-step'), frame.id);
  }
  assert.equal(frame.state.userReturned, false, 'queue view ends before the syscall return');
}

async function assertBackends(page, everyStep = false) {
  const select = page.locator('[data-vbr-backend-select]');
  const catalog = JSON.parse(await page.locator('#block-io-scenarios-data').textContent());
  assert.deepEqual(await select.locator('option').evaluateAll(items => items.map(item => item.value)),
    backendIds);
  assert.equal(await page.locator('#backends').count(), 0, 'no separate comparison section');
  assert(await select.evaluate(node => Boolean(node.closest('[data-vbr-lab]'))));
  const signatures = new Set();
  for (const backend of backendIds) for (const operation of ['read', 'write']) {
    const activeScene = model.getScenario(baseScene, operation, backend, catalog);
    const trace = model.buildTrace(activeScene);
    const queue = model.buildQueueTrace(activeScene);
    await page.locator('[data-vbr-view="overview"]').click();
    await select.selectOption(backend);
    await page.locator(`[data-vbr-operation="${operation}"]`).click();
    await page.locator('[data-vbr-reset]').click();
    const lab = page.locator('[data-vbr-lab]');
    assert.equal(await lab.getAttribute('data-backend'), backend);
    assert.equal(await lab.getAttribute('data-step'), trace[0].id);
    await assertPhaseWrapping(page);
    await page.locator('[data-vbr-view="mechanism"]').click();
    await assertPhaseWrapping(page);
    await assertQueueFrame(page, model.buildQueueInitial(activeScene), activeScene);
    if (everyStep) {
      await page.locator('[data-vbr-next]').click();
      for (const [index, frame] of queue.entries()) {
        await assertQueueFrame(page, frame, activeScene);
        if (index < queue.length - 1) await page.locator('[data-vbr-next]').click();
      }
    } else {
      await page.goto(`${route}?backend=${backend}&operation=${operation}&view=mechanism&step=${trace.at(-1).id}`);
      await assertQueueFrame(page, queue.at(-1), activeScene);
    }
    assert(await page.locator('[data-vbr-next]').isDisabled(), 'queue view stops at descriptor reclamation');
    assert.equal(await page.locator('[data-vbr-queue-detail] [data-queue-ring="used"] .walkthrough-queue-slots > div').first().locator('small').last().textContent(),
      `len ${trace.at(-1).state.usedLength}`);
    if (backend.endsWith('scsi')) assert.match(await page.locator('[data-vbr-queue-detail] .walkthrough-queue-response').textContent(), /response OK.*sense_len 0/);
    await page.locator('[data-vbr-view="source"]').click();
    await assertCallpaths(page, queue.at(-1), activeScene);
    if (everyStep) {
      await page.locator('[data-vbr-reset]').click();
      for (const [index, frame] of trace.entries()) {
        await assertFrame(page, frame, activeScene);
        if (index < trace.length - 1) await page.locator('[data-vbr-next]').click();
      }
    } else {
      await page.goto(`${route}?backend=${backend}&operation=${operation}&view=source&step=${trace.at(-1).id}`);
    }
    await assertCallpaths(page, trace.at(-1), activeScene);
    signatures.add(activeScene.steps.map(step => step.sourceIds.join(',')).join('|'));
    await page.reload({waitUntil: 'domcontentloaded'});
    assert.equal(await lab.getAttribute('data-backend'), backend);
    assert.equal(await lab.getAttribute('data-operation'), operation);
    assert.equal(await lab.getAttribute('data-step'), trace.at(-1).id);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.locator('[data-vbr-view="overview"]').click();
    assert.equal(await page.locator('[data-vbr-device-domain]').textContent(), backend.startsWith('spdk') ? 'SPDK' : 'QEMU');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  }
  assert.equal(signatures.size, 10, 'all ten selections have distinct source-backed traces');
}

async function assertCallpaths(page, frame, activeScene = scene) {
  const paths = page.locator('[data-vbr-callpaths]');
  assert(await paths.isVisible(), 'source view displays the source call paths');
  assert(await page.locator('[data-vbr-board]').isHidden(), 'source view does not duplicate the overview board');
  assert(await page.locator('[data-vbr-structure]').isHidden(), 'source view only shows source paths');
  assert.equal(await page.locator('[data-vbr-sources], [data-vbr-source-list]').count(), 0,
    'source references are unified in the left source view');
  const active = await paths.locator('[data-source-id][data-current="true"]')
    .evaluateAll(items => items.map(item => item.dataset.sourceId));
  const includedSources = new Set(activeScene.callPaths.flatMap(group => group.sourceIds));
  const expectedActive = [...new Set(frame.sourceIds)];
  assert(expectedActive.length > 0, `${frame.id}: at least one referenced source appears in the call paths`);
  assert.deepEqual([...new Set(active)].sort(), expectedActive.sort(), `${frame.id}: active source entries`);
  const groups = await paths.locator('[data-callpath]').evaluateAll(items => items.map(item => ({
    active: item.dataset.current === 'true',
    sources: [...item.querySelectorAll('[data-source-id]')].map(source => source.dataset.sourceId),
  })));
  assert.equal(groups.length, activeScene.callPaths.length);
  for (const group of groups) {
    assert.equal(group.active, group.sources.some(id => frame.sourceIds.includes(id)), 'source group follows the active step');
  }
  const supplement = frame.sourceIds.filter(id => !includedSources.has(id));
  assert.deepEqual(await paths.locator('[data-source-supplement] [data-source-id]')
    .evaluateAll(items => items.map(item => item.dataset.sourceId)), supplement);
  assert.equal(await paths.locator('[data-source-supplement]').isVisible(), supplement.length > 0);
  for (const item of await paths.locator('[data-source-id]').evaluateAll(items => items.map(item => ({
    id: item.dataset.sourceId, href: item.querySelector('a').href,
  })))) assert.equal(item.href, activeScene.sources[item.id].url);
}

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const errors = [];
  try {
    if (process.env.VBR_SUMMARY_ONLY === '1') {
      for (const width of [1440, 390]) {
        const page = await browser.newPage({viewport: {width, height: 1000}, reducedMotion: 'reduce'});
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${route}#walkthrough`, {waitUntil: 'networkidle'});
        await page.locator('[data-vbr-lab][data-initialized="true"]').waitFor();
        const catalog = JSON.parse(await page.locator('#block-io-scenarios-data').textContent());
        for (const backend of backendIds) {
          await page.locator('[data-vbr-backend-select]').selectOption(backend);
          for (const operation of ['read', 'write']) {
            await page.locator(`[data-vbr-operation="${operation}"]`).click();
            const activeScene = catalog.routes.find(item => item.id === backend).scenarios[operation];
            await assertSingleSummary(page, activeScene);
            if (screenshots && operation === 'read' && ['local-scsi', 'rbd-blk'].includes(backend)) {
              fs.mkdirSync(screenshots, {recursive: true});
              await page.locator('[data-vbr-operation-controls]').scrollIntoViewIfNeeded();
              await page.screenshot({path: path.join(screenshots, `virtio-summary-${backend}-${width}.png`)});
            }
          }
        }
        // Returning from a populated SPDK configuration must not leave stale details in local-blk.
        await page.locator('[data-vbr-backend-select]').selectOption('local-blk');
        await page.locator('[data-vbr-operation="read"]').click();
        await assertSingleSummary(page, catalog.routes.find(item => item.id === 'local-blk').scenarios.read);
        console.log(`single summary ${width}px: all 10 backend/operation selections passed`);
        await page.close();
      }
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.VBR_PHASES_ONLY === '1') {
      for (const width of [320, 390, 700, 768, 1024, 1440]) {
        const page = await browser.newPage({viewport: {width, height: 1000}, reducedMotion: 'reduce'});
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${route}#walkthrough`, {waitUntil: 'networkidle'});
        await page.locator('[data-vbr-lab][data-initialized="true"]').waitFor();
        for (const backend of backendIds) {
          await page.locator('[data-vbr-backend-select]').selectOption(backend);
          for (const operation of ['read', 'write']) {
            await page.locator(`[data-vbr-operation="${operation}"]`).click();
            await assertPhaseWrapping(page);
            const stage = page.locator('[data-vbr-phase="read"]');
            if (backend === 'rbd-blk') assert.match(await stage.textContent(), /RBD/);
            if (backend.startsWith('spdk-')) assert.match(await stage.textContent(), /NVMe/);
            for (const phase of ['return', 'application']) {
              await page.locator(`[data-vbr-phase="${phase}"]`).click();
              assert.equal(await page.locator('[data-vbr-phase][aria-current="step"]').getAttribute('data-vbr-phase'), phase);
              await assertPhaseWrapping(page);
            }
            await page.locator('[data-vbr-view="mechanism"]').click();
            await assertPhaseWrapping(page);
            await page.locator('[data-vbr-view="overview"]').click();
            if (screenshots && backend === 'rbd-blk' && operation === 'read' && [1440, 390].includes(width)) {
              fs.mkdirSync(screenshots, {recursive: true});
              await page.locator('[data-vbr-progress]').scrollIntoViewIfNeeded();
              await page.screenshot({path: path.join(screenshots, `virtio-blk-phases-${width}.png`)});
            }
          }
        }
        console.log(`phase wrapping ${width}px: all 10 backend/operation selections passed`);
        await page.close();
      }
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.VBR_LAYOUT_ONLY === '1') {
      for (const width of [1440, 1024, 768, 701, 700, 390, 320]) {
        const page = await browser.newPage({viewport: {width, height: 1000}, reducedMotion: 'reduce'});
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${route}?backend=local-scsi#walkthrough`, {waitUntil: 'networkidle'});
        await page.locator('[data-vbr-lab][data-initialized="true"]').waitFor();
        const configuration = page.locator('details[data-vbr-configuration]');
        const control = configuration.locator('[data-vbr-control-plane]');
        assert.equal(await configuration.evaluate(node => node.open), false);
        assert(await control.isHidden());
        await configuration.locator('summary').click();
        assert(await control.isVisible());
        assert((await control.textContent()).trim());
        await configuration.locator('summary').click();
        assert(await control.isHidden());
        for (const operation of ['read', 'write']) {
          await page.locator(`[data-vbr-operation="${operation}"]`).click();
          await assertScenarioHeader(page);
        }
        if (screenshots && [1440, 390, 320].includes(width)) {
          fs.mkdirSync(screenshots, {recursive: true});
          await page.locator('[data-vbr-operation-controls]').scrollIntoViewIfNeeded();
          await page.screenshot({path: path.join(screenshots, `virtio-blk-selector-${width}.png`)});
        }
        console.log(`scenario selector ${width}px: passed`);
        await page.close();
      }
      assert.deepEqual(errors, []);
      return;
    }
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    page.on('pageerror', error => errors.push(error.message));
    const lab = page.locator('[data-vbr-lab]');
    const button = name => page.locator(`[data-vbr-${name}]`);
    await page.goto(`${base}/mechanisms.html`, {waitUntil: 'networkidle'});
    assert.equal(await page.locator('.mechanism-walkthrough-entry').count(), 0);
    assert.equal(await page.locator('a[href="mechanisms/virtio-blk-read.html"]').count(), 1);
    assert.equal(await page.locator('a[href="mechanisms/virtio-blk-read.html#walkthrough"]').count(), 0);
    assert.equal(await page.locator('#block-io-walkthrough a[href="mechanisms/virtio-blk-read.html"]').count(), 1);
    assert.equal(await page.locator('#device-virtualization a[href="mechanisms/virtio-blk-read.html"]').count(), 0);
    await page.goto(route, {waitUntil: 'networkidle'});
    await page.locator('[data-vbr-lab][data-initialized="true"]').waitFor();
    assert.equal(await lab.getAttribute('data-step'), firstFrame.id);
    assert.equal(await lab.getAttribute('data-operation'), 'read');
    assert.equal(await button('play').getAttribute('aria-pressed'), 'false');
    assert(await button('structure').isHidden());
    assert(await button('callpaths').isHidden());
    assert(await button('board').isVisible());
    await assertView(page, 'overview');
    assert(await button('fallback').isHidden());
    assert.equal(await page.locator('[data-vbr-share],[data-vbr-share-fallback],[data-vbr-share-url]').count(), 0);
    assert.deepEqual(await page.locator('[data-vbr-view]').allTextContents(), ['流程总览', '队列机制', '源码路径']);
    assert.equal(await page.locator('.vbr-guest-user [data-vbr-node="application"]').count(), 1);
    assert.equal(await page.locator('.vbr-guest-kernel [data-vbr-node="driver"]').count(), 1);
    assert.equal(await page.locator('.vbr-guest-kernel [data-vbr-node="reclaim"]').count(), 1);
    await page.locator('[data-vbr-view="mechanism"]').click();
    assert(await button('board').isHidden());
    assert(await button('structure').isVisible());
    assert(await button('callpaths').isHidden());
    await assertView(page, 'mechanism');
    await assertQueueFrame(page, queueInitial, scene);
    await button('next').click();
    let ownedSlotColor;
    for (const [index, frame] of queueFrames.entries()) {
      await assertQueueFrame(page, frame, scene);
      if (frame.state.usedPublished && !frame.state.reclaimed) {
        ownedSlotColor = await page.locator('[data-vbr-queue-detail] [data-queue-ring="used"] .walkthrough-queue-slots > div').first()
          .evaluate(node => { const style = getComputedStyle(node); return [style.backgroundColor, style.borderStyle, style.color]; });
      }
      if (index < queueFrames.length - 1) await button('next').click();
    }
    assert(await button('next').isDisabled());
    assert.match(await page.locator('[data-vbr-queue-detail] [data-queue-ring="used"] .walkthrough-queue-slots').textContent(), /len 4097/);
    assert.match(await button('ownership').textContent(), /回收/);
    const returnedSlotColor = await page.locator('[data-vbr-queue-detail] [data-queue-ring="used"] .walkthrough-queue-slots > div').first()
      .evaluate(node => { const style = getComputedStyle(node); return [style.backgroundColor, style.borderStyle, style.color]; });
    assert.notDeepEqual(returnedSlotColor, ownedSlotColor, 'reclaimed records are visually distinct from owned ones');
    assert.equal(returnedSlotColor[1], 'dashed', 'shared queue marks retained consumed slots with a dashed border');
    for (const frame of queueFrames) {
      await page.locator(`[data-vbr-queue-step="${frame.id}"]`).click();
      await assertQueueFrame(page, frame, scene);
    }
    await button('prev').click();
    assert.equal(await lab.getAttribute('data-step'), queueFrames.at(-2).id);
    assert.equal(await page.locator('[data-vbr-node="application"]').getAttribute('data-done'), 'false');
    await button('reset').click();
    assert.equal(await lab.getAttribute('data-step'), queueInitial.id);
    assert.match(await page.locator('[data-vbr-queue-detail] .walkthrough-queue-response').textContent(), /未回写/);
    assert(await button('prev').isDisabled());
    // URLs from the earlier full-step queue view resolve to an existing queue milestone.
    for (const oldId of [firstFrame.id, 'signal-irqfd', finalFrame.id]) {
      const original = frames.find(frame => frame.id === oldId);
      const projected = [...queueFrames].reverse().find(frame => frame.index <= original.index) || queueInitial;
      await page.goto(`${route}?view=mechanism&step=${oldId}`, {waitUntil: 'networkidle'});
      await assertQueueFrame(page, projected, scene);
      assert.equal(new URL(page.url()).searchParams.get('step'), projected.id,
        'old queue URLs are normalized to the visible milestone');
      await page.reload({waitUntil: 'networkidle'});
      await assertQueueFrame(page, projected, scene);
    }
    await button('reset').click();
    await button('speed').selectOption('1200');
    await button('play').click();
    await page.waitForFunction(initial => document.querySelector('[data-vbr-lab]').dataset.step !== initial,
      queueInitial.id);
    await button('play').click();
    assert.equal(await button('play').getAttribute('aria-pressed'), 'false');

    await page.locator('[data-vbr-view="overview"]').click();
    await page.locator('[data-vbr-node="buffer"]').click();
    assert.equal(await lab.getAttribute('data-step'), dataFrame.id);
    await page.locator('[data-vbr-view="source"]').click();
    assert.equal(await lab.getAttribute('data-step'), dataFrame.id, 'view does not reset progress');
    await assertCallpaths(page, dataFrame);
    await assertView(page, 'source');
    const restored = new URL(page.url());
    assert.equal(restored.searchParams.get('step'), dataFrame.id);
    assert.equal(restored.searchParams.get('view'), 'source');
    await page.reload({waitUntil: 'networkidle'});
    assert.equal(await lab.getAttribute('data-step'), dataFrame.id);
    assert.equal(await lab.getAttribute('data-view'), 'source');
    await assertCallpaths(page, dataFrame);
    await button('next').click();
    const afterRead = frames[frames.findIndex(frame => frame.id === dataFrame.id) + 1];
    assert.equal(await lab.getAttribute('data-step'), afterRead.id);
    await assertCallpaths(page, afterRead);

    await button('reset').click();
    for (const [index, frame] of frames.entries()) {
      await assertFrame(page, frame, scene);
      await assertCallpaths(page, frame);
      if (index < frames.length - 1) await button('next').click();
    }

    await page.locator('[data-vbr-operation="write"]').click();
    assert.equal(await lab.getAttribute('data-operation'), 'write');
    assert.equal(await lab.getAttribute('data-step'), writeFrames[0].id);
    assert.equal(await lab.getAttribute('data-view'), 'source', 'operation change preserves the chosen view');
    await assertCallpaths(page, writeFrames[0], writeScene);
    assert.equal(new URL(page.url()).searchParams.get('operation'), 'write');
    await page.locator('[data-vbr-view="mechanism"]').click();
    await assertView(page, 'mechanism');
    await assertQueueFrame(page, model.buildQueueInitial(writeScene), writeScene);
    await button('next').click();
    for (const [index, frame] of writeQueueFrames.entries()) {
      await assertQueueFrame(page, frame, writeScene);
      if (index < writeQueueFrames.length - 1) await button('next').click();
    }
    assert.match(await page.locator('[data-vbr-queue-detail] [data-queue-ring="used"] .walkthrough-queue-slots').textContent(), /len 1/);
    assert.match(await page.locator('[data-vbr-queue-detail] [data-buffer="payload"] strong').textContent(), /输入数据/);
    assert.equal(await page.locator('[data-vbr-queue-detail] [data-descriptor="1"] td').nth(3).textContent(), 'NEXT',
      'write data is device-readable, unlike read result descriptors');
    assert.match(await button('ownership').textContent(), /回收/);
    await page.locator('[data-vbr-view="source"]').click();
    await assertCallpaths(page, writeQueueFrames.at(-1), writeScene);
    while (!(await button('next').isDisabled())) await button('next').click();
    await assertCallpaths(page, writeFrames.at(-1), writeScene);
    assert.match(await button('ownership').textContent(), /pwrite\(\).*flush \/ FUA/);
    await page.reload({waitUntil: 'networkidle'});
    assert.equal(await lab.getAttribute('data-operation'), 'write');
    assert.equal(await lab.getAttribute('data-step'), writeFrames.at(-1).id);
    assert.equal(await lab.getAttribute('data-view'), 'source');
    await assertCallpaths(page, writeFrames.at(-1), writeScene);
    await page.locator('[data-vbr-operation="read"]').click();
    assert.equal(await lab.getAttribute('data-operation'), 'read');
    assert.equal(await lab.getAttribute('data-step'), firstFrame.id);
    assert.equal(await lab.getAttribute('data-view'), 'source');
    assert.equal(new URL(page.url()).searchParams.get('operation'), null);
    await assertCallpaths(page, firstFrame);

    await button('reset').click();
    await button('speed').selectOption('1200');
    await button('play').click();
    await page.waitForFunction(initial => document.querySelector('[data-vbr-lab]').dataset.step !== initial, firstFrame.id);
    await button('play').click();
    assert.equal(await button('play').getAttribute('aria-pressed'), 'false');
    await page.locator('[data-vbr-view="overview"]').click();
    assert(await button('board').isVisible());
    assert(await button('callpaths').isHidden());
    await button('reset').click();
    let count = 0;
    const overviewVisited = [];
    while (!(await button('next').isDisabled())) {
      await button('next').click();
      overviewVisited.push(await lab.getAttribute('data-step'));
      assert(++count < frames.length, 'overview advances without looping');
    }
    assert(overviewVisited.includes('pending-interrupt'));
    assert(overviewVisited.includes('inject-interrupt'));
    for (const frame of frames.filter(frame => frame.edge?.kind === 'data' || frame.changes.dataReady)) {
      assert(overviewVisited.includes(frame.id), `${frame.id}: overview keeps actual data transfers`);
    }
    assert.equal(await lab.getAttribute('data-step'), finalFrame.id);
    assert.match(await button('ownership').textContent(), /应用可读取/);
    await assertBackends(page, true);
    await page.goto(`${route}?step=unknown&view=unknown&operation=unknown&backend=unknown`);
    assert.equal(await lab.getAttribute('data-step'), firstFrame.id);
    assert.equal(await lab.getAttribute('data-view'), 'overview');
    assert.equal(await lab.getAttribute('data-operation'), 'read');
    await page.close();

    for (const width of [1440, 1024, 768, 390, 320]) {
      const responsive = await browser.newPage({viewport: {width, height: width < 700 ? 844 : 1000}, reducedMotion: 'reduce'});
      responsive.on('pageerror', error => errors.push(error.message));
      await responsive.goto(`${route}?step=${dataFrame.id}&view=mechanism#walkthrough`, {waitUntil: 'networkidle'});
      await assertView(responsive, 'mechanism');
      const layout = await responsive.evaluate(() => {
        const toolbar = document.querySelector('.vbr-toolbar').getBoundingClientRect();
        const controls = [...document.querySelectorAll('.vbr-toolbar button,.vbr-toolbar select')]
          .filter(node => node.getBoundingClientRect().width);
        const prose = document.querySelector('.mechanism-prose').getBoundingClientRect();
        const textBlocks = ['preface', 'sources'].flatMap(id => {
          const section = document.getElementById(id);
          return [section, ...section.querySelectorAll(':scope > p')].map(node => {
            const rect = node.getBoundingClientRect();
            return {section: id, tag: node.tagName, left: rect.left, right: rect.right, width: rect.width};
          });
        });
        return {width: innerWidth, page: document.documentElement.scrollWidth,
          prose: {left: prose.left, right: prose.right, width: prose.width}, textBlocks,
          board: document.querySelector('[data-vbr-board]').getBoundingClientRect().width,
          structure: document.querySelector('[data-vbr-structure]').getBoundingClientRect().width,
          minControlHeight: Math.min(...controls.map(node => node.getBoundingClientRect().height)),
          minOperationHeight: Math.min(...[...document.querySelectorAll('[data-vbr-operation]')]
            .map(node => node.getBoundingClientRect().height)),
          minNodeFont: Math.min(...[...document.querySelectorAll('.vbr-node strong')].map(node => parseFloat(getComputedStyle(node).fontSize))),
          toolbar: {left: toolbar.left, right: toolbar.right},
          controlsContained: controls.every(node => {
            const box = node.getBoundingClientRect();
            return box.left >= toolbar.left - 1 && box.right <= toolbar.right + 1;
          }),
          activePhaseVisible: (() => {
            const strip = document.querySelector('[data-vbr-progress]').getBoundingClientRect();
            const current = document.querySelector('[data-vbr-phase][aria-current="step"], [data-vbr-queue-step][aria-current="step"]');
            if (!current) return true; // Queue preparation has not started.
            const active = current.getBoundingClientRect();
            return active.left >= strip.left - 1 && active.right <= strip.right + 1;
          })()};
      });
      assert(layout.page <= width + 1, JSON.stringify(layout));
      assert(layout.board <= width + 1, JSON.stringify(layout));
      assert(layout.structure > 0 && layout.structure <= width + 1, JSON.stringify(layout));
      assert(layout.toolbar.left >= 0 && layout.toolbar.right <= width + 1, JSON.stringify(layout));
      assert(layout.minControlHeight >= 44, JSON.stringify(layout));
      assert(layout.minOperationHeight >= 44, JSON.stringify(layout));
      assert(layout.controlsContained, JSON.stringify(layout));
      assert(layout.minNodeFont >= 14, JSON.stringify(layout));
      assert(layout.activePhaseVisible, JSON.stringify(layout));
      for (const block of layout.textBlocks) {
        assert(Math.abs(block.width - layout.prose.width) <= 2
          && Math.abs(block.left - layout.prose.left) <= 2
          && Math.abs(block.right - layout.prose.right) <= 2,
        `${width}px: explanatory text uses the full prose width: ${JSON.stringify({block, prose: layout.prose})}`);
      }
      if (screenshots && [1440, 390, 320].includes(width)) {
        fs.mkdirSync(screenshots, {recursive: true});
        await responsive.locator('[data-vbr-structure]').scrollIntoViewIfNeeded();
        await responsive.screenshot({path: path.join(screenshots, `virtio-blk-read-${width}.png`)});
      }
      await responsive.locator('[data-vbr-view="overview"]').click();
      await assertView(responsive, 'overview');
      assert(await responsive.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        `${width}px: overview must not overflow the page`);
      if (screenshots && [1440, 390, 320].includes(width)) {
        await responsive.locator('[data-vbr-board]').scrollIntoViewIfNeeded();
        await responsive.screenshot({path: path.join(screenshots, `virtio-blk-board-${width}.png`)});
      }
      await responsive.locator('[data-vbr-view="source"]').click();
      await assertCallpaths(responsive, dataFrame);
      await assertView(responsive, 'source');
      assert(await responsive.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        `${width}px: source call paths must not overflow the page`);
      if (screenshots && [1440, 390, 320].includes(width)) {
        await responsive.locator('[data-vbr-callpaths]').scrollIntoViewIfNeeded();
        await responsive.screenshot({path: path.join(screenshots, `virtio-blk-source-${width}.png`)});
      }
      await responsive.locator('[data-vbr-backend-select]').selectOption('local-scsi');
      await assertScenarioHeader(responsive);
      await assertBackends(responsive);
      assert(await responsive.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        `${width}px: backend scenarios must not overflow the page`);
      console.log(`layout ${width}px: passed`);
      await responsive.close();
    }
    const context = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 844}});
    const fallback = await context.newPage();
    await fallback.goto(route);
    assert(await fallback.locator('[data-vbr-fallback]').isVisible());
    assert.equal(await fallback.locator('[data-vbr-fallback] li').count(), scene.phases.length);
    assert(await fallback.locator('[data-vbr-controls]').isHidden());
    assert(await fallback.locator('[data-vbr-operation-controls]').isHidden());
    assert(await fallback.locator('[data-vbr-backend-select]').isHidden());
    assert.equal(await fallback.locator('#backends').count(), 0);
    assert(await fallback.locator('#sources').isVisible());
    await context.close();
    assert.deepEqual(errors, []);
    console.log('Read/write lifecycle, distinct views, interrupt injection, source paths, backend selection, URL restoration, responsive layout and no-JS fallback: passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
