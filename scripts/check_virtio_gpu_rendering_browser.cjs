/* Check the GPU command/result pictures against the offline execution trace. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const model = require('../virt_report/render/assets/execution-model.js');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.GPU_SCREENSHOT_DIR;
if (shots) fs.mkdirSync(shots, {recursive: true});

async function blockRemote(context) {
  await context.route('**/*', route => ['localhost', '127.0.0.1', '[::1]'].includes(
    new URL(route.request().url()).hostname) ? route.continue() : route.abort());
}

async function assertPicture(page, expected) {
  const actual = await page.locator('[data-execution-lab]').evaluate(lab => {
    const panel = lab.querySelector('[data-gpu-command-visual]');
    return {
      count: lab.querySelectorAll('[data-gpu-command-visual]').length,
      scene: panel.dataset.scenario,
      command: panel.dataset.commandPhase,
      output: panel.dataset.outputPhase,
      step: lab.executionSnapshot.frame.id,
      state: lab.executionSnapshot.frame.values,
      ready: Object.fromEntries([...panel.querySelectorAll('[data-gpu-resource]')]
        .map(node => [node.dataset.gpuResource, node.dataset.ready])),
      packets: Object.fromEntries([...panel.querySelectorAll('[data-gpu-packet]')]
        .map(node => [node.dataset.gpuPacket, node.querySelector('code').textContent])),
      result: panel.querySelector('[data-gpu-resource="result"] .gpu-command-resource-status').textContent,
      text: panel.textContent,
      pageWidth: document.documentElement.scrollWidth,
      viewport: innerWidth,
      animations: [...panel.querySelectorAll('[data-changed="true"]')]
        .map(node => getComputedStyle(node).animationName)
    };
  });
  assert.equal(actual.count, 1, 'state changes must reuse one visual panel');
  assert.equal(actual.step, expected.id);
  assert.deepEqual(actual.state, expected.values, `${actual.scene}/${expected.id}: trace and view agree`);
  assert.equal(actual.command, expected.values.commandPhase);
  assert.equal(actual.output, expected.values.outputPhase);
  assert.deepEqual(actual.ready, {
    input: String(expected.values.inputPhase === 'ready'),
    host: String(['host-ready', 'guest-ready'].includes(expected.values.outputPhase)),
    result: String(expected.values.outputPhase === 'guest-ready')
  });
  assert.deepEqual(actual.packets, {
    header: expected.values.submitHeader,
    batch: expected.values.commandBatch,
    response: expected.values.response
  });
  if (expected.values.outputPhase === 'guest-ready') assert.equal(actual.result,
    expected.values.appResult === '待完成' ? '同步依赖已满足，等待应用消费' : expected.values.appResult);
  assert(actual.pageWidth <= actual.viewport + 1, `${actual.scene}/${expected.id}: no document overflow`);
  assert(!/\d+(?:\.\d+)?\s*%/.test(actual.text), 'teaching states must not invent execution percentages');
  assert(actual.animations.every(name => name === 'none'), 'reduced-motion preference is respected');
  assert.equal(await page.locator('[data-execution-lab] .walkthrough-packet').count(), 0,
    'reduced-motion disables travelling arrow particles');
}

async function goToFrame(lab, frame) {
  await lab.locator('[data-execution-view="overview"]').click();
  await lab.evaluate((lab, target) => {
    for (let n = 0; lab.executionSnapshot.frame.index !== target && n < 120; n++) {
      const direction = lab.executionSnapshot.frame.index < target ? 'next' : 'prev';
      lab.querySelector(`[data-execution-${direction}]`).click();
    }
    if (lab.executionSnapshot.frame.index !== target) throw new Error(`Cannot reach frame ${target}`);
  }, frame.index);
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}, reducedMotion: 'reduce'});
  await blockRemote(context);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({width, height: 1100});
      const response = await page.goto(`${base}/mechanisms/virtio-gpu-rendering.html`);
      assert.equal(response.status(), 200);
      const lab = page.locator('[data-execution-lab]');
      await lab.locator('[data-execution-controls]').waitFor({state: 'visible'});
      await lab.locator('[data-gpu-command-visual]').waitFor({state: 'attached'});
      const demo = await lab.locator('#execution-demo-data').evaluate(node => JSON.parse(node.textContent));
      assert.deepEqual(demo.scenarios.map(scene => scene.id).sort(),
        ['virgl-decode', 'virgl-encode', 'virgl-render']);
      for (const scene of demo.scenarios) {
        await lab.locator('[data-execution-scenario]').selectOption(scene.id);
        const trace = model.buildTrace(scene);
        await assertPicture(page, trace[0]);
        assert(await lab.locator('[data-execution-prev]').isDisabled(), 'scene switch resets progress');

        const prepared = trace.find(frame => frame.values.inputPhase === 'ready');
        assert(prepared, 'input preparation is observable');
        await goToFrame(lab, prepared);
        await assertPicture(page, prepared);

        const gpu = trace.find(frame => frame.id === 'execute-gpu');
        assert(gpu && gpu.values.outputPhase === 'host-ready', 'GPU completion precedes Guest confirmation');
        await goToFrame(lab, gpu);
        await assertPicture(page, gpu);
        assert.equal(await lab.locator('[data-gpu-resource="result"]').getAttribute('data-ready'), 'false');

        const signalled = trace.find(frame => frame.id === 'signal-guest-fence');
        const consumed = trace.find(frame => frame.id === 'consume-result');
        assert(signalled && consumed && signalled.index < consumed.index,
          'fence signalling and application consumption are separate steps');
        await goToFrame(lab, signalled);
        await assertPicture(page, signalled);
        await goToFrame(lab, consumed);
        await assertPicture(page, consumed);
        assert.notEqual(signalled.values.appResult, consumed.values.appResult,
          'the application only consumes the result at the final step');

        // Seeking backwards must remove results that have not been produced yet.
        await goToFrame(lab, gpu);
        await assertPicture(page, gpu);
        await lab.locator('[data-execution-reset]').click();
        await assertPicture(page, trace[0]);
        if (shots && [390, 1440].includes(width)) {
          await goToFrame(lab, consumed);
          await lab.locator('[data-gpu-command-visual]').screenshot({
            path: path.join(shots, `${scene.id}-results-${width}.png`)
          });
        }
      }
      console.log(`PASS GPU input/host/Guest results, seeking/reset and scenario switching: ${width}px`);
    }

    // The supplementary pictures follow the same timer and view controls.
    const lab = page.locator('[data-execution-lab]');
    await lab.locator('[data-execution-scenario]').selectOption('virgl-render');
    await lab.locator('[data-execution-speed]').selectOption('1200');
    await lab.locator('[data-execution-play]').click();
    assert.equal(await lab.locator('[data-execution-play]').getAttribute('aria-pressed'), 'true');
    await page.waitForFunction(() => document.querySelector('[data-execution-lab]').executionSnapshot.frame.index > 0);
    await lab.locator('[data-execution-play]').click();
    assert.equal(await lab.locator('[data-execution-play]').getAttribute('aria-pressed'), 'false');
    const snapshot = await lab.evaluate(node => node.executionSnapshot);
    await assertPicture(page, snapshot.frame);
    await lab.locator('[data-execution-view="structure"]').click();
    assert(await lab.locator('[data-gpu-command-visual]').isHidden(), 'resource pictures belong to overview');
    await lab.locator('[data-execution-view="overview"]').click();
    assert(await lab.locator('[data-gpu-command-visual]').isVisible());

    // Force the portable fullscreen path; common controls keep working inside it.
    await lab.evaluate(node => { node.requestFullscreen = undefined; });
    await lab.locator('[data-walkthrough-expand]').click();
    assert.equal(await lab.getAttribute('data-walkthrough-expanded'), 'fallback');
    await lab.locator('[data-execution-next]').click();
    await assertPicture(page, await lab.evaluate(node => node.executionSnapshot.frame));
    await page.keyboard.press('Escape');
    assert.equal(await lab.locator('[data-walkthrough-expand]').getAttribute('aria-pressed'), 'false');
    assert.deepEqual(errors, []);
    console.log('PASS GPU shared playback/speed, views, fullscreen and no runtime errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
