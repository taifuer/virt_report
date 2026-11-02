/* Offline regression for integrated R1/F1/FUA playback; no external requests. */
'use strict';
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');

(async () => {
  const browser = await chromium.launch({args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  await context.route('**/*', route => route.request().url().startsWith(base + '/')
    ? route.continue() : route.abort());
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let snapshots = 0;
  try {
    for (const width of [1440,390,320]) {
      await page.setViewportSize({width,height:1000});
      for (const [backend,mode] of [['local-blk','flush'],['local-scsi','fua']]) {
        await page.goto(`${base}/mechanisms/virtio-blk-read.html?backend=${backend}&operation=write&completion=${mode}`);
        assert.equal(await page.locator('[data-execution-lab]').count(),0,'only one disk player');
        assert.equal(await page.locator('[data-vbr-completion]').inputValue(),mode);
        for (const view of ['overview','mechanism','source']) {
          await page.locator(`[data-vbr-view="${view}"]`).click();
          await page.locator('[data-vbr-reset]').click();
          const result = await page.evaluate(async ({backend,mode,view}) => {
            const lab = document.querySelector('[data-vbr-lab]');
            const one = selector => lab.querySelector(selector);
            const baseScene = JSON.parse(document.querySelector('#virtio-blk-read-data').textContent);
            const catalog = JSON.parse(document.querySelector('#block-io-scenarios-data').textContent);
            const scene = VirtioBlkReadModel.getScenario(baseScene,'write',backend,catalog);
            const expected = DiskCompletionModel.extend(scene,VirtioBlkReadModel.buildTrace(scene),mode).frames;
            const checked = [];
            for (let count=0;count<100;count++) {
              await new Promise(resolve => requestAnimationFrame(resolve));
              const id=lab.dataset.step, frame=expected.find(item=>item.id===id);
              if (!frame) throw new Error(`Unknown ${mode}/${view} step: ${id}`);
              const stable = one('[data-vbr-durability]').textContent.includes('本次数据已获持久化成功保证');
              if (stable !== frame.durability.stableConfirmed) throw new Error(`${id}: wrong durability guarantee`);
              if (view==='source' && one('[data-vbr-title]').textContent!==frame.title) throw new Error(`${id}: wrong title`);
              if (view==='source') for (const sourceId of frame.sourceIds) {
                // Paths deliberately omit helper calls; the inspector must still
                // show every step reference, and included path nodes must light up.
                const source=scene.sources[sourceId];
                const links=[...one('[data-vbr-callpaths]').querySelectorAll('a')];
                if (!links.some(link=>link.href===source.url && link.textContent.includes(source.symbol)))
                  throw new Error(`${id}: inspector omitted ${sourceId}`);
                const node=lab.querySelector(`[data-source-id="${sourceId}"]`);
                if (!node || node.dataset.current!=='true') throw new Error(`${id}: source not highlighted ${sourceId}`);
              }
              if (frame.followup && view==='mechanism') {
                const queue=one('[data-vbr-queue-detail]');
                if (one('[data-vbr-structure]').hidden || !queue.textContent.includes('F1')) throw new Error(`${id}: stale R1 queue`);
                for (const name of ['avail','used']) {
                  const ring=queue.querySelector(`[data-queue-ring="${name}"]`);
                  if (!ring.textContent.includes(`${name}.idx = ${frame.queueState[name].index}`)) {
                    throw new Error(`${id}: incorrect ${name} index`);
                  }
                }
              }
              if (mode==='fua' && one('[data-vbr-node="application"] .vbr-mode-tag').textContent!=='请求边界') {
                throw new Error('FUA must not impersonate a userspace syscall');
              }
              if (mode==='fua' && one('[data-vbr-driver-title]').textContent!=='SCSI 与块层') {
                throw new Error('FUA must start from the existing block request');
              }
              if (mode==='fua' && view==='mechanism' && frame.changes.dataReady
                  && one('[data-vbr-detail]').textContent!==frame.detail) {
                throw new Error('Queue projection must preserve the FUA completion contract');
              }
              if (document.documentElement.scrollWidth>innerWidth+1) throw new Error(`${id}/${innerWidth}: page overflow`);
              if (lab.querySelector('.walkthrough-inspector a')) throw new Error('Inspector duplicates source references');
              const progress=one('[data-vbr-progress]');
              if (progress.scrollWidth>progress.clientWidth+1) throw new Error(`${id}: phase overflow`);
              checked.push(id);
              if (one('[data-vbr-next]').disabled) break;
              one('[data-vbr-next]').click();
            }
            if (!one('[data-vbr-next]').disabled) throw new Error('Playback did not reach the end');
            if (view==='source' && JSON.stringify(checked)!==JSON.stringify(expected.map(frame=>frame.id))) {
              throw new Error('Source view must expose every causal step');
            }
            return {checked,last:lab.dataset.step};
          }, {backend,mode,view});
          snapshots += result.checked.length;
          await page.reload();
          assert.equal(await page.locator('[data-vbr-completion]').inputValue(),mode);
          assert.equal(await page.locator('[data-vbr-lab]').getAttribute('data-step'),result.last,'deep-link restoration');
          assert.equal(await page.locator('[data-vbr-lab]').getAttribute('data-view'),view);
        }
        console.log(`PASS ${backend}/${mode}: all views, causal steps and deep links at ${width}px`);
      }
    }
    // A backend without a reviewed persistence path must never inherit one.
    await page.locator('[data-vbr-backend-select]').selectOption('rbd-blk');
    assert.equal(await page.locator('[data-vbr-completion]').inputValue(),'ordinary');
    await page.locator('[data-vbr-operation="read"]').click();
    assert(await page.locator('[data-vbr-completion-choice]').isHidden());
    assert(await page.locator('[data-vbr-durability]').isHidden());
    assert.deepEqual(errors,[]);
    console.log(`PASS ${snapshots} disk completion snapshots; no console errors`);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
