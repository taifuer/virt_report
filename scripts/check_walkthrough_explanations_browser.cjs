/* Local-only regression: one continuous explanation, with every reviewed detail retained. */
'use strict';
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname), 'use a local preview');
const routes = ['network-io', 'cpu-execution', 'memory-access', 'interrupt-delivery',
  'gpu-execution', 'virtio-gpu-rendering', 'libvirt-domain-start'];
const diskCases = [
  ['local-blk', 'read', 'ordinary'], ['local-blk', 'write', 'ordinary'],
  ['local-scsi', 'read', 'ordinary'], ['local-scsi', 'write', 'ordinary'],
  ['rbd-blk', 'read', 'ordinary'], ['rbd-blk', 'write', 'ordinary'],
  ['spdk-blk', 'read', 'ordinary'], ['spdk-blk', 'write', 'ordinary'],
  ['spdk-scsi', 'read', 'ordinary'], ['spdk-scsi', 'write', 'ordinary'],
  ['local-blk', 'write', 'flush'], ['local-scsi', 'write', 'fua'],
];

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
    ? route.continue() : route.abort());
  // Shared assertions inspect the UI, without changing application state or data.
  await context.addInitScript(() => {
    window.checkStepExplanation = (lab, parts, tag) => {
      const fail = message => { throw new Error(`${tag}: ${message}`); };
      const body = lab.querySelector('.walkthrough-inspector .walkthrough-step-body');
      const paragraph = body?.querySelector(':scope > p.walkthrough-step-explanation[data-step-explanation]');
      if (!paragraph || body.children.length !== 1) fail('one explanation paragraph is required');
      const seen = new Set();
      for (const [part, expected] of Object.entries(parts)) {
        const node = paragraph.querySelector(`[data-step-part="${part}"]`);
        const text = String(expected || '').trim();
        const key = text.replace(/\s+/g, ' ').replace(/[。.!！?？;；]+$/u, '');
        if (!node || node.tagName !== 'SPAN') fail(`${part} must remain an inline span`);
        if (node.textContent !== text) fail(`${part} omitted or changed reviewed text`);
        if (node.hidden !== (!text || seen.has(key))) fail(`${part} hidden for a nonduplicate reason`);
        if (text) seen.add(key);
      }
      if (paragraph.querySelectorAll('[data-step-part]').length !== Object.keys(parts).length)
        fail('unexpected explanation fragments');
      const inspector = body.closest('.walkthrough-inspector');
      if (inspector.querySelector('.walkthrough-step-context, .execution-connection'))
        fail('independent handoff text must not duplicate the paragraph');
      for (const node of lab.querySelectorAll('[data-execution-connections], [data-vbr-event]')) {
        if (inspector.contains(node) || !node.classList.contains('sr-only'))
          fail('diagram handoff labels must remain screen-reader-only outside the explanation');
      }
    };
    window.checkExplanationLayout = lab => {
      const paragraph = lab.querySelector('[data-step-explanation]');
      const bounds = paragraph.getBoundingClientRect();
      const spans = [...paragraph.querySelectorAll('[data-step-part]')].filter(node => !node.hidden);
      const boxes = spans.flatMap(node => {
        const range = document.createRange(); range.selectNodeContents(node);
        return [...range.getClientRects()];
      });
      const fail = message => { throw new Error(`${lab.dataset.scenario || lab.dataset.backend}/${lab.dataset.view}/${innerWidth}: ${message}`); };
      if (!spans.length || !bounds.height) fail('explanation is not visible');
      if (spans.some(node => getComputedStyle(node).display !== 'inline')) fail('explanation fragments split into blocks');
      if (boxes.some(box => box.left < bounds.left - 1 || box.right > bounds.right + 1)) fail('text escapes its paragraph');
      if (paragraph.scrollWidth > paragraph.clientWidth + 1) fail('paragraph scrolls horizontally');
      if (lab.scrollWidth > lab.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1)
        fail('walkthrough or page overflows horizontally');
      if (getComputedStyle(paragraph).fontSize !== '16px') fail('body text size changed');
    };
  });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let snapshots = 0, layoutChecks = 0;
  async function checkViewsAndExpansion(prefix, views) {
    const lab = page.locator(`[data-${prefix}-lab]`);
    for (const expanded of [false, true]) {
      if (expanded) await lab.locator('[data-walkthrough-expand]').click();
      for (const view of views) {
        await lab.locator(`[data-${prefix}-view="${view}"]`).click();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await lab.evaluate(root => window.checkExplanationLayout(root));
        layoutChecks++;
      }
    }
    await lab.locator('[data-walkthrough-expand]').click();
  }
  try {
    for (const width of [1366, 390, 320]) {
      await page.setViewportSize({width, height: width === 1366 ? 900 : 844});
      for (const slug of routes) {
        await page.goto(`${base}/mechanisms/${slug}.html`);
        await page.locator('[data-execution-controls]').waitFor({state: 'visible'});
        const result = await page.evaluate(() => {
          const lab = document.querySelector('[data-execution-lab]');
          const demo = JSON.parse(document.querySelector('#execution-demo-data').textContent);
          const one = selector => lab.querySelector(selector);
          const choose = scene => {
            const categories = demo.selector.categories || [{id: '', groups: demo.selector.groups}];
            const category = categories.find(c => c.groups.some(g => g.scenarios.some(s => s.id === scene.id)));
            const group = category.groups.find(g => g.scenarios.some(s => s.id === scene.id));
            const control = one('[data-execution-category]');
            if (control && control.value !== category.id) {
              control.value = category.id; control.dispatchEvent(new Event('change', {bubbles: true}));
            }
            if (demo.selector.operations) one(`[data-execution-operation="${group.operation}"]`).click();
            const select = one('[data-execution-scenario]');
            if (select.value !== scene.id) {
              select.value = scene.id; select.dispatchEvent(new Event('change', {bubbles: true}));
            }
          };
          let checked = 0, longest = {size: -1};
          for (const scene of demo.scenarios) {
            choose(scene);
            const trace = ExecutionDemoModel.buildTrace(scene);
            for (const view of ['overview', 'structure', 'source']) {
              one(`[data-execution-view="${view}"]`).click(); one('[data-execution-reset]').click();
              const navigation = ExecutionDemoModel.navigation(scene, trace, view);
              const navButtons = [...lab.querySelectorAll('[data-execution-step]')];
              if (navButtons.length !== navigation.length) throw new Error(`${scene.id}/${view}: wrong navigation size`);
              if (view !== 'structure' && (navButtons.length > 10 || navButtons.length !== scene.phases.length))
                throw new Error(`${scene.id}: overview/source navigation should show reviewed phases`);
              navButtons.forEach((button, n) => {
                if (button.querySelector('strong').textContent !== navigation[n].title)
                  throw new Error(`${scene.id}: phase title mismatch`);
                button.click();
                if (lab.executionSnapshot.frame.index !== navigation[n].index)
                  throw new Error(`${scene.id}: phase jump skipped its first operation`);
              });
              one('[data-execution-reset]').click();
              for (const [position, index] of ExecutionDemoModel.stops(trace, view).entries()) {
                if (position) one('[data-execution-next]').click();
                const frame = trace[index], tag = `${demo.id}/${scene.id}/${view}/${frame.id}`;
                if (lab.dataset.step !== frame.id || lab.dataset.scenario !== scene.id) throw new Error(`${tag}: wrong state`);
                const current = lab.querySelector('[data-execution-step][aria-current="step"]');
                if (!current || Number(current.dataset.executionStep) > index || Number(current.dataset.executionEnd) < index)
                  throw new Error(`${tag}: active phase must contain the current operation`);
                if (view !== 'structure' && index > 0) {
                  const phaseIndex = navigation.findIndex(phase => phase.index <= index && index <= phase.end);
                  const phase = navigation[phaseIndex], start = Math.max(1, phase.index);
                  const peers = navigation.filter(item => item.branch === phase.branch);
                  const branch = scene.parallel?.branches.find(branch => branch.id === phase.branch);
                  const label = branch ? `${branch.title} · 阶段 ${peers.indexOf(phase) + 1} / ${peers.length}`
                    : `${scene.parallel ? '共同前序' : '阶段'} ${peers.indexOf(phase) + 1} / ${peers.length}`;
                  const counter = `${label} · 阶段内第 ${index - start + 1} / ${phase.end - start + 1} 步`;
                  if (one('[data-execution-counter]').textContent !== counter)
                    throw new Error(`${tag}: neutral initial state must not count as an action`);
                }
                checkStepExplanation(lab, {description: frame.description, detail: frame.detail}, tag);
                checked++;
                const size = frame.description.length + frame.detail.length;
                if (size > longest.size) longest = {size, scene: scene.id, step: frame.id};
              }
              if (!one('[data-execution-next]').disabled) throw new Error(`${scene.id}/${view}: not at final step`);
            }
          }
          return {checked, scenarios: demo.scenarios.length, longest};
        });
        snapshots += result.checked;
        // Longest prose exercises both wide and narrow layouts, not only initial boilerplate.
        await page.goto(`${base}/mechanisms/${slug}.html?scenario=${result.longest.scene}&step=${result.longest.step}`);
        await page.locator('[data-execution-controls]').waitFor({state: 'visible'});
        await checkViewsAndExpansion('execution', ['overview', 'structure', 'source']);
        console.log(`PASS ${slug}: ${result.scenarios} scenarios, ${result.checked} explanation snapshots, inline/fullscreen at ${width}px`);
      }
      for (const [backend, operation, mode] of diskCases) {
        await page.goto(`${base}/mechanisms/virtio-blk-read.html?backend=${backend}&operation=${operation}&completion=${mode}`);
        await page.locator('[data-vbr-controls]').waitFor({state: 'visible'});
        const checked = await page.evaluate(({backend, operation, mode}) => {
          const lab = document.querySelector('[data-vbr-lab]'), one = selector => lab.querySelector(selector);
          const baseScene = JSON.parse(document.querySelector('#virtio-blk-read-data').textContent);
          const catalog = JSON.parse(document.querySelector('#block-io-scenarios-data').textContent);
          const scene = VirtioBlkReadModel.getScenario(baseScene, operation, backend, catalog);
          const frames = DiskCompletionModel.extend(scene, VirtioBlkReadModel.buildTrace(scene), mode).frames;
          const original = VirtioBlkReadModel.getScenario(baseScene, operation, backend, catalog);
          const queueFrames = VirtioBlkReadModel.buildQueueTrace(original).map(frame => {
            const current = frames.find(item => item.id === frame.id);
            return mode === 'fua' && current.changes.dataReady
              ? {...frame, queueDescription: current.description, queueDetail: current.detail} : frame;
          });
          let initial = VirtioBlkReadModel.buildQueueInitial(original);
          if (mode === 'fua') initial = {...frames[0], queueDescription: frames[0].description, queueDetail: frames[0].detail};
          queueFrames.unshift(initial);
          const followups = new Set(['flush-prepare', 'flush-fill-avail', 'flush-publish', 'flush-consume',
            'flush-status', 'flush-fill-used', 'flush-used', 'flush-reclaim']);
          for (const frame of frames.filter(item => followups.has(item.id))) queueFrames.push({...frame,
            queueDescription: frame.description, queueDetail: frame.detail});
          const expectedState = frame => {
            const s = frame.state;
            if (frame.followup) return frame.id === 'sync-return'
              ? 'R1 与独立刷新 F1 均成功，fsync 返回。' : 'R1 已完成；正在跟踪后续独立刷新 F1。';
            if (mode === 'fua') return s.completed
              ? '上层块请求 R1 完成，数据已获持久化保证；不推断应用调用返回。' : '跟踪已有 REQ_FUA 的 R1；不模拟应用系统调用。';
            const text = s.userReturned ? (operation === 'write'
              ? 'pwrite() 已返回，可复用缓冲区；本演示未执行 flush / FUA' : 'pread() 已返回，应用可读取结果')
              : s.completed ? '块请求结束，应用调用尚未返回'
                : s.bioCompleted ? 'bio 等待条件已满足，块请求与直接 I/O 路径仍需清理'
                  : s.reclaimed ? '描述符已回收，数据映射待清理'
                    : s.usedPublished ? '设备已完成，等待 Guest 消费完成'
                      : s.published ? '设备可访问，驱动必须保持缓冲区有效'
                        : s.pagesMapped ? '用户页已固定，尚未发布给设备' : '用户缓冲区尚未交给设备';
            return text + '。';
          };
          let count = 0;
          for (const view of ['overview', 'mechanism', 'source']) {
            one(`[data-vbr-view="${view}"]`).click(); one('[data-vbr-reset]').click();
            const visited = [];
            for (let guard = 0; guard < 120; guard++) {
              const frame = frames.find(item => item.id === lab.dataset.step);
              if (!frame) throw new Error(`${backend}/${mode}: unknown disk step`);
              const projected = view === 'mechanism' ? queueFrames.find(item => item.id === frame.id) : null;
              if (view === 'mechanism' && !projected) throw new Error(`${frame.id}: missing queue projection`);
              checkStepExplanation(lab, {
                description: projected?.queueDescription || frame.description,
                detail: projected?.queueDetail || frame.detail,
                state: expectedState(frame),
              }, `${backend}/${operation}/${mode}/${view}/${frame.id}`);
              visited.push(frame.id); count++;
              if (one('[data-vbr-next]').disabled) break;
              one('[data-vbr-next]').click();
            }
            if (!one('[data-vbr-next]').disabled) throw new Error('disk playback did not finish');
            if (view === 'source' && JSON.stringify(visited) !== JSON.stringify(frames.map(f => f.id)))
              throw new Error('source view must retain all disk causal steps');
          }
          return count;
        }, {backend, operation, mode});
        snapshots += checked;
        await checkViewsAndExpansion('vbr', ['overview', 'mechanism', 'source']);
      }
      console.log(`PASS disk: ${diskCases.length} read/write/flush/FUA cases, all views, inline/fullscreen at ${width}px`);
    }
    assert.deepEqual(errors, [], 'no browser exceptions');
    console.log(`PASS ${snapshots} complete explanation snapshots and ${layoutChecks} responsive/fullscreen layouts; external requests blocked`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
