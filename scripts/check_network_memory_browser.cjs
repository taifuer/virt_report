/* Network overview and queue detail must render the same offline typed snapshot. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const model = require('../virt_report/render/assets/execution-model.js');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.NETWORK_MEMORY_SCREENSHOT_DIR || '/tmp/network-memory-review';
const arrowsOnly = process.env.NETWORK_MEMORY_ARROWS_ONLY === '1';
fs.mkdirSync(shots, {recursive:true});

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function seekFrame(page, target) {
  await page.locator('[data-execution-lab]').evaluate((lab, target) => {
    for (let n = 0; lab.executionSnapshot.frame.index !== target && n < 120; n++) {
      const direction = lab.executionSnapshot.frame.index < target ? 'next' : 'prev';
      lab.querySelector(`[data-execution-${direction}]`).click();
    }
    if (lab.executionSnapshot.frame.index !== target) throw new Error(`Cannot reach frame ${target}`);
  }, target);
}

// Kept serializable so every step is inspected without changing the application model.
function readMemory() {
  const lab = document.querySelector('[data-execution-lab]');
  const {frame,scene,view} = lab.executionSnapshot;
  const root = lab.querySelector('[data-execution-network-memory]');
  const bounds = root.getBoundingClientRect();
  const panels = [...root.querySelectorAll('[data-network-memory-part]')].map(panel => ({
    id:panel.dataset.networkMemoryPart, current:panel.dataset.current, text:panel.textContent,
    chain:panel.querySelector('.network-memory-chain')?.textContent,
    pointers:[...panel.querySelectorAll('.network-memory-pointer')].map(node=>node.textContent),
    index:panel.querySelector('.network-memory-index')?.textContent,
    slots:[...panel.querySelectorAll('.network-memory-slots > span')].map(slot=>({
      text:slot.textContent, filled:slot.dataset.filled, published:slot.dataset.published,
      returned:slot.dataset.returned, title:slot.title,
    })),
    buffers:[...panel.querySelectorAll('[data-network-buffer]')].map(buffer=>{
      const meter=buffer.querySelector('[role="meter"]');
      return {id:buffer.dataset.networkBuffer,state:buffer.dataset.state,text:buffer.textContent,
        valid:Number(meter.getAttribute('aria-valuenow')),capacity:Number(meter.getAttribute('aria-valuemax')),
        min:Number(meter.getAttribute('aria-valuemin')),width:parseFloat(meter.firstElementChild.style.width)};
    }),
  }));
  const detail=lab.querySelector('[data-execution-structures] [data-typed-queue]');
  const r=lab.getBoundingClientRect();
  return {frame,meta:scene.queue,view,panels,tableCount:root.querySelectorAll('table').length,
    bounds:{left:bounds.left,right:bounds.right},documentWidth:document.documentElement.scrollWidth,
    viewport:innerWidth,lab:{scroll:lab.scrollWidth,client:lab.clientWidth,left:r.left,right:r.right},
    detail:{tables:detail.querySelectorAll('.walkthrough-descriptors').length,
      descriptors:[...detail.querySelectorAll('tbody tr')].map(row=>({id:Number(row.dataset.descriptor),state:row.dataset.state,text:row.textContent})),
      rings:Object.fromEntries([...detail.querySelectorAll('[data-queue-ring]')].map(ring=>[
        ring.dataset.queueRing,[...ring.querySelectorAll('.walkthrough-queue-slots > div')].map(slot=>slot.querySelector('span').textContent)]))},
    nodes:Object.fromEntries([...lab.querySelectorAll('[data-execution-node]')].map(node=>[
      node.dataset.executionNode,node.querySelector('.execution-node-status').textContent])),
  };
}

function assertMemory(actual, frame, scene, tag) {
  assert.equal(actual.frame.id,frame.id,`${tag}: playback position`);
  assert.deepEqual(actual.frame.queueState,frame.queueState,`${tag}: model snapshot`);
  assert.deepEqual(actual.nodes,frame.nodes,`${tag}: cumulative node status`);
  assert.deepEqual(actual.panels.map(panel=>panel.id),['descriptors','buffers','completion'],`${tag}: three memory regions`);
  assert.equal(actual.tableCount,0,`${tag}: full descriptor table belongs in the structure view`);
  assert(actual.documentWidth<=actual.viewport+1,`${tag}: document horizontal overflow ${JSON.stringify(actual.lab)}`);
  assert(actual.lab.scroll<=actual.lab.client+1,`${tag}: walkthrough horizontal overflow ${JSON.stringify(actual.lab)}`);
  if(actual.view==='overview') {
    assert(actual.bounds.left>=-1 && actual.bounds.right<=actual.viewport+1,`${tag}: memory escaped viewport`);
  }
  assert.deepEqual([actual.meta.headerLength,actual.meta.packetLength,actual.meta.payloadLength],[12,60,18]);
  const state=frame.queueState;
  const [desc,buffers,done]=actual.panels;
  const changed=(...keys)=>(state.changed||[]).some(value=>keys.some(key=>value===key||value.startsWith(key+'.')));
  assert.equal(desc.current,String(changed('descriptors','avail')),`${tag}: descriptor highlight`);
  assert.equal(buffers.current,String(changed('buffers','ownership')),`${tag}: buffer highlight`);
  assert.equal(done.current,String(changed('used','freeDescriptors')),`${tag}: completion highlight`);
  assert.equal(desc.chain,`链 ${state.descriptors.length?state.descriptors.map(d=>d.index).join(' → '):'尚未填写'}`);
  for(const d of state.descriptors) assert(desc.pointers.includes(
    `desc[${d.index}] → 0x${d.addr.toString(16)} · ${d.len} B · ${d.flags.includes('WRITE')?'设备可写':'设备只读'}`),`${tag}: descriptor pointer`);
  for(const [panel,name] of [[desc,'avail'],[done,'used']]) {
    const ring=state[name];
    assert.equal(panel.index,`${name}.idx ${ring.index} · ${name==='avail'?'设备消费':'驱动回收'} ${ring.consumer}`);
    assert.equal(panel.slots.length,scene.queue.size,`${tag}: ${name} ring size`);
    for(const [index,slot] of panel.slots.entries()) {
      const entry=ring.slots[index];
      assert.equal(slot.text,entry==null?'·':String(name==='avail'?entry:entry.id),`${tag}: ${name} slot ${index}`);
      assert.equal(slot.filled,String(entry!=null));
      assert.equal(slot.published,String(entry!=null && index<ring.index));
      assert.equal(slot.returned,String(entry!=null && index<ring.consumer));
      if(name==='used' && entry!=null) assert(slot.title.includes(`len ${entry.len}`));
    }
  }
  assert.deepEqual(buffers.buffers.map(buffer=>buffer.id),state.buffers.map(buffer=>buffer.id),`${tag}: buffer lifetime`);
  for(const [index,buffer] of buffers.buffers.entries()) {
    const expected=state.buffers[index];
    assert.equal(buffer.state,expected.state);assert.equal(buffer.valid,expected.validBytes);
    assert.equal(buffer.capacity,expected.length);assert.equal(buffer.min,0);
    assert(Math.abs(buffer.width-Math.min(100,100*expected.validBytes/expected.length))<0.0001,`${tag}: valid-byte meter`);
    if(scene.queue.name==='RX' && expected.validBytes>0) assert(buffer.text.includes(
      `12 B virtio-net 头 + ${expected.validBytes-12} B Ethernet 帧`),`${tag}: RX bytes include virtio header`);
  }
  assert(done.pointers.includes(`空闲描述符 ${state.freeDescriptors} / ${scene.queue.size}`));
  const completion=state.used.slots.find(record=>record!=null);
  assert.equal(done.chain,completion?`id ${completion.id} · len ${completion.len}`:'尚无完成项');
  assert.equal(actual.detail.tables,1,`${tag}: full queue remains available`);
  if(state.descriptors.length) assert.deepEqual(actual.detail.descriptors.map(row=>row.id),state.descriptors.map(d=>d.index));
  else assert(actual.detail.descriptors.every(row=>row.state==='unprepared'));
  for(const name of ['avail','used']) assert.deepEqual(actual.detail.rings[name],state[name].slots.map(entry=>
    entry==null?'—':name==='avail'?`头 ${entry}`:`id ${entry.id}`));
}

async function switchView(page,view) {
  await page.locator(`[data-execution-view="${view}"]`).click();
  await settle(page);
}

async function assertMemoryArrows(page,demo) {
  let checked=0;
  const events=new Set(['publish','publish-buffer','replenish','kick','kick-refill','fill-used','used',
    'notify','route-interrupt','prepare-interrupt','guest-interrupt']);
  for(const viewport of [{width:320,height:740},{width:390,height:844},{width:768,height:1024},
    {width:1366,height:768},{width:1440,height:900}]) {
    await page.setViewportSize(viewport);
    for(const scene of demo.scenarios) {
      const frames=model.buildTrace(scene).filter(frame=>events.has(frame.id));
      await page.goto(`${base}/mechanisms/network-io.html?scenario=${scene.id}`);
      await page.waitForFunction(id=>document.querySelector('[data-execution-lab]')?.executionSnapshot?.scene.id===id,scene.id);
      await page.locator('[data-walkthrough-expand]').first().click();
      if(viewport.width >= 1366) {
        const rows=await page.locator('.walkthrough-domains').evaluate(domains =>
          new Set([...domains.children].map(domain=>Math.round(domain.getBoundingClientRect().top))).size);
        assert.equal(rows,1,`${scene.id}/${viewport.width}: Guest, KVM and backend domains belong on one row`);
      }
      for(const frame of frames) {
        await seekFrame(page, frame.index);
        await settle(page);
        const actual=await page.locator('[data-execution-lab]').evaluate(lab=>{
          const svg=lab.querySelector('.execution-arrows'),box=svg.viewBox.baseVal;
          return {step:lab.dataset.step,view:lab.dataset.view,expanded:lab.classList.contains('walkthrough-expanded'),
            width:box.width,height:box.height,links:lab.executionSnapshot.frame.links,
            paths:[...svg.querySelectorAll('path')].map(path=>{
              const b=path.getBBox();
              return {kind:path.dataset.kind,length:path.getTotalLength(),d:path.getAttribute('d'),
                left:b.x,top:b.y,right:b.x+b.width,bottom:b.y+b.height};
            })};
        });
        const tag=`${scene.id}/${viewport.width}/${frame.id}`;
        assert.equal(actual.step,frame.id);assert.equal(actual.view,'overview');assert(actual.expanded);
        assert.deepEqual(actual.links,frame.links,`${tag}: expected handoff is active`);
        assert.equal(actual.paths.length,frame.links.length*2,`${tag}: each active handoff needs a route and arrowhead`);
        const expectedKinds=frame.links.flatMap(id=>{
          const edge=scene.links.find(link=>link.id===id);return [edge.kind,edge.kind];
        });
        assert.deepEqual(actual.paths.map(path=>path.kind),expectedKinds,`${tag}: arrow types`);
        for(const route of actual.paths) {
          assert(route.length>0 && !route.d.includes('NaN'),`${tag}: nonempty arrow geometry`);
          assert(route.left>=-1 && route.top>=-1 && route.right<=actual.width+1 && route.bottom<=actual.height+1,
            `${tag}: arrow must remain inside its SVG viewBox ${JSON.stringify(route)}`);
        }
        if(frame.links.length) checked++;
        if(scene.id==='qemu-rx' && viewport.width===1440 && frame.id==='used') {
          await page.screenshot({path:path.join(shots,'qemu-rx-1440-used-arrow.png')});
        }
        if(viewport.width===1366 && ['qemu-tx','vhost-tx','dpdk-tx','vdpa-tx'].includes(scene.id)
            && ['kick','prepare-interrupt','guest-interrupt'].includes(frame.id)) {
          await page.screenshot({path:path.join(shots,`${scene.id}-1366-${frame.id}.png`)});
        }
      }
      // Normal presets exercise publication, backend notification and actual Guest IRQ entry.
      if(scene.id==='qemu-tx' || scene.id==='qemu-rx') assert.deepEqual(frames.map(frame=>frame.id),
        [scene.id==='qemu-tx'?'publish':'publish-buffer','kick','fill-used','used','notify',
          'route-interrupt','prepare-interrupt','guest-interrupt',...(scene.id==='qemu-rx'?['kick-refill']:[])]);
      await page.locator('[data-walkthrough-expand]').first().click();
      await page.waitForFunction(()=>!document.fullscreenElement &&
        !document.querySelector('[data-execution-lab]').classList.contains('walkthrough-expanded'));
      console.log(`PASS arrows ${scene.id}/${viewport.width}: ${frames.filter(frame=>frame.links.length).length} active handoffs; inactive steps retain no arrows`);
    }
  }
  assert(checked>0,'at least one network memory arrow was tested');
  return checked;
}

(async()=>{
  const browser=await chromium.launch({args:['--no-sandbox']});
  const context=await browser.newContext({reducedMotion:'reduce'});
  await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  let checked=0;
  try {
    await page.goto(`${base}/mechanisms/network-io.html`);
    await page.waitForFunction(()=>document.querySelector('[data-execution-lab]')?.executionSnapshot);
    const demo=JSON.parse(await page.locator('#execution-demo-data').textContent());
    assert.equal(demo.scenarios.length,10,'all ten curated network scenarios');
    if(!arrowsOnly) for(const viewport of [{width:320,height:740},{width:390,height:844},{width:768,height:1024},{width:1440,height:900}]) {
      await page.setViewportSize(viewport);
      for(const scene of demo.scenarios) {
        const trace=model.buildTrace(scene),tag=`${scene.id}/${viewport.width}`;
        await page.goto(`${base}/mechanisms/network-io.html?scenario=${scene.id}`);
        await page.waitForFunction(id=>document.querySelector('[data-execution-lab]')?.executionSnapshot?.scene.id===id,scene.id);
        await settle(page);
        // Both the overview and full queue are rendered from the snapshot at every step.
        for(const frame of trace) {
          assertMemory(await page.evaluate(readMemory),frame,scene,`${tag}/${frame.id}`);checked++;
          if(frame.index<trace.length-1) {
            await page.locator('[data-execution-next]').evaluate(button=>button.click());
            await settle(page);
          }
        }
        const projected=model.project(trace,'structure',trace.length-1);
        await switchView(page,'structure');
        assertMemory(await page.evaluate(readMemory),trace[projected],scene,`${tag}/structure`);
        assert.equal(await page.locator('[data-execution-next]').isDisabled(),true);
        for(const view of ['source','overview']) {
          await switchView(page,view);
          assertMemory(await page.evaluate(readMemory),trace[projected],scene,`${tag}/${view}`);
        }
        // Reset restores empty memory, not retained RX packets or old completion slots.
        await page.locator('[data-execution-reset]').evaluate(button=>button.click());
        await settle(page);
        assertMemory(await page.evaluate(readMemory),trace[0],scene,`${tag}/reset`);
        // Freeze after completion is filled but before used.idx publication.
        const filled=trace.find(frame=>frame.id==='fill-used');
        await seekFrame(page, filled.index);
        await settle(page);
        assertMemory(await page.evaluate(readMemory),filled,scene,`${tag}/unpublished-completion`);
        if(['qemu-rx','qemu-tx','dpdk-tx','qemu-rx-no-buffer'].includes(scene.id)) {
          await page.locator('[data-execution-network-memory]').scrollIntoViewIfNeeded();
          await page.screenshot({path:path.join(shots,`${scene.id}-${viewport.width}-memory.png`)});
        }
        // Widening the same frame must not reinitialize any protocol state.
        await page.locator('[data-walkthrough-expand]').first().click();
        await settle(page);
        assertMemory(await page.evaluate(readMemory),filled,scene,`${tag}/expanded`);
        if(scene.id==='qemu-rx') await page.screenshot({path:path.join(shots,`${scene.id}-${viewport.width}-expanded.png`)});
        await page.locator('[data-walkthrough-expand]').first().click();
        await settle(page);
        assertMemory(await page.evaluate(readMemory),filled,scene,`${tag}/restored`);
        console.log(`PASS ${tag}: ${trace.length} snapshots, queue/source view projection, reset, resize and expansion`);
      }
    }
    const arrows=await assertMemoryArrows(page,demo);
    assert.deepEqual(errors,[],'no browser errors');
    console.log(`PASS ${checked} network memory snapshots and ${arrows} arrow frames; screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
