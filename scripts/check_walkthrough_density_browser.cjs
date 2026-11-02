/* Visual density: overview memory, full queue detail and wrapping source chains. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.WALKTHROUGH_DENSITY_SCREENSHOT_DIR || '/tmp/walkthrough-density-review';
fs.mkdirSync(shots, {recursive:true});

const pages = [
  {slug:'virtio-blk-read', prefix:'vbr', detail:'mechanism', overview:'[data-vbr-board]',
    structure:'[data-vbr-structure]', query:''},
  {slug:'network-io', prefix:'execution', detail:'structure', overview:'[data-execution-overview]',
    structure:'[data-execution-structures]', query:'?scenario=qemu-rx'},
];
pages.push(
  {...pages[0],name:'rbd-read',query:'?backend=rbd-blk'},
  {...pages[0],name:'spdk-scsi-write',query:'?backend=spdk-scsi&operation=write'},
  {...pages[1],name:'dpdk-tx',query:'?scenario=dpdk-tx'},
);

async function settle(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function showView(page, item, view) {
  await page.locator(`[data-${item.prefix}-view="${view}"]`).click();
  await settle(page);
}

async function alignOverview(page, item, fullscreen) {
  await page.evaluate(({selector,fullscreen}) => {
    const lab=document.querySelector('[data-walkthrough-lab]');
    // Normal reading remains full-width. The expanded desktop canvas shares
    // its workspace with an inspector and keeps controls above both panels.
    const top=lab.querySelector(selector).getBoundingClientRect().top;
    if (fullscreen) lab.scrollTop = 0;
    else window.scrollTo(0,window.scrollY+top-76);
  }, {selector:item.overview,fullscreen});
  await settle(page);
}

async function assertOverview(page, item, viewport, fullscreen) {
  const layout=await page.locator(item.overview).evaluate(root => {
    const summary=root.querySelector('.walkthrough-queue--compact');
    const r=root.getBoundingClientRect(), sr=summary?.getBoundingClientRect();
    const workspace=root.closest('.walkthrough-workspace').getBoundingClientRect();
    const canvas=root.closest('.execution-canvas, .vbr-visual');
    const cr=canvas.getBoundingClientRect(), inspector=root.closest('.walkthrough-workspace').querySelector('.walkthrough-inspector').getBoundingClientRect();
    const lab=root.closest('[data-walkthrough-lab]');
    return {height:r.height,bottom:r.bottom,top:r.top,diagramWidth:r.width,workspaceWidth:workspace.width,page:document.documentElement.scrollWidth,width:innerWidth,
      canvas:{top:cr.top,bottom:cr.bottom,left:cr.left,right:cr.right,height:cr.height,scroll:canvas.scrollWidth,client:canvas.clientWidth,overflow:getComputedStyle(canvas).overflowY},
      inspector:{left:inspector.left,right:inspector.right,top:inspector.top,bottom:inspector.bottom},
      lab:{scroll:lab.scrollWidth,client:lab.clientWidth},
      memory:[...root.querySelectorAll('[data-network-memory-part]')].map(panel=>({id:panel.dataset.networkMemoryPart,
        slots:panel.querySelectorAll('.network-memory-slots > span').length,buffers:panel.querySelectorAll('[data-network-buffer]').length})),
      tables:root.querySelectorAll('table').length,
      summary:summary ? {height:sr.height,text:summary.textContent,
        fields:[...summary.querySelectorAll('[data-queue-summary-field]')].map(node=>node.dataset.queueSummaryField),
        tables:summary.querySelectorAll('table').length,
        slots:summary.querySelectorAll('.walkthrough-queue-slots').length,
        buffers:summary.querySelectorAll('.walkthrough-queue-buffers').length} : null};
  });
  const tag=`${item.slug}/${viewport.width}/${fullscreen?'fullscreen':'normal'}`;
  assert.equal(layout.tables,0,`${tag}: overview must not repeat the detailed descriptor table`);
  if(item.slug==='network-io') {
    assert.deepEqual(layout.memory.map(panel=>panel.id),['descriptors','buffers','completion'],`${tag}: network memory remains split into three regions`);
    assert.equal(layout.memory[0].slots,8,`${tag}: available ring slots`);
    assert.equal(layout.memory[2].slots,8,`${tag}: used ring slots`);
    assert(layout.memory[1].buffers>0,`${tag}: packet buffers remain visible`);
    assert.equal(layout.summary,null,`${tag}: do not repeat a second compact queue summary`);
  } else {
    assert(layout.summary,`${tag}: overview retains compact queue state`);
    assert.equal(layout.summary.tables+layout.summary.slots+layout.summary.buffers,0,
      `${tag}: compact means a summary, not another full queue visualization`);
    assert.deepEqual(layout.summary.fields,['chain','avail','used','free'],
      `${tag}: compact queue keeps request identity and publication/reclamation progress`);
    assert(layout.summary.height <= (viewport.width>=1000?120:190),`${tag}: summary too tall ${JSON.stringify(layout)}`);
  }
  assert(layout.page<=layout.width+1,`${tag}: page overflow`);
  assert(layout.lab.scroll<=layout.lab.client+1,`${tag}: walkthrough horizontal overflow`);
  assert(layout.canvas.scroll<=layout.canvas.client+1,`${tag}: canvas horizontal overflow`);
  if (viewport.width>=1000) {
    if(fullscreen) {
      assert(layout.canvas.top>=120 && layout.canvas.top<=220,`${tag}: controls must leave useful room for the drawing ${JSON.stringify(layout)}`);
      assert(layout.canvas.height<=viewport.height-180 && layout.canvas.bottom<=viewport.height+1,`${tag}: expanded canvas is bounded by the viewport ${JSON.stringify(layout)}`);
      assert(layout.canvas.right<=layout.inspector.left && Math.abs(layout.canvas.top-layout.inspector.top)<2,
        `${tag}: diagram and current explanation share a row ${JSON.stringify(layout)}`);
      assert(['auto','scroll'].includes(layout.canvas.overflow),`${tag}: longer content remains accessible by scrolling`);
      assert(layout.diagramWidth>=layout.workspaceWidth*.6,`${tag}: drawing retains most of the expanded workspace`);
    } else {
      assert(layout.diagramWidth>=layout.workspaceWidth-44,`${tag}: normal reading remains full-width ${JSON.stringify(layout)}`);
      if(item.slug!=='network-io') {
        assert(layout.height<=viewport.height-180,`${tag}: core disk diagram exceeds its screen budget ${JSON.stringify(layout)}`);
        assert(layout.bottom<=viewport.height-8,`${tag}: aligned disk diagram fits the viewport ${JSON.stringify(layout)}`);
      }
    }
  }
  return layout;
}

async function assertDetail(page,item,viewport) {
  const detail=page.locator(item.structure);
  assert(await detail.isVisible());
  assert.equal(await detail.locator('.walkthrough-descriptors').count(),1,'full descriptor table remains in detail view');
  assert.equal(await detail.locator('.walkthrough-queue-rings').count(),1);
  for (const name of ['avail','used']) {
    assert.equal(await detail.locator(`[data-queue-ring="${name}"] .walkthrough-queue-slots > div`).count(),8);
  }
  const hasColumns=await detail.locator('.walkthrough-descriptors thead th').allTextContents();
  assert.deepEqual(hasColumns,['描述符 / 缓冲区','addr','len','flags','next']);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${item.slug}/${viewport.width}: detail overflow`);
}

async function assertSources(page,slug,width) {
  const chains=await page.locator('.walkthrough-callpath > ol').evaluateAll(lists=>lists.map(list=>{
    const b=list.getBoundingClientRect();
    const items=[...list.children].map(item=>{
      const r=item.getBoundingClientRect(),a=item.querySelector('a'),ar=a.getBoundingClientRect();
      const arrow=getComputedStyle(item,'::before').content;
      const explicit=[...item.querySelectorAll('[aria-hidden="true"]')].some(n=>n.textContent.includes('→'));
      const text=[...a.querySelectorAll('strong,span')].flatMap(n=>{
        const range=document.createRange();range.selectNodeContents(n);
        return [...range.getClientRects()].filter(r=>r.width>0).map(r=>({left:r.left,right:r.right}));
      });
      return {top:r.top,left:r.left,right:r.right,arrow,explicit,anchor:{left:ar.left,right:ar.right},text};
    });
    return {left:b.left,right:b.right,items,display:getComputedStyle(list).display,wrap:getComputedStyle(list).flexWrap};
  }));
  assert(chains.some(chain=>chain.items.length>1),`${slug}: meaningful function chain is present`);
  let horizontal=false;
  for(const chain of chains) {
    assert.equal(chain.display,'flex',`${slug}: function path should be a horizontal chain`);
    assert.equal(chain.wrap,'wrap',`${slug}: function path wraps instead of clipping`);
    for(const [index,item] of chain.items.entries()) {
      assert(item.left>=chain.left-1 && item.right<=chain.right+1,`${slug}/${width}: source card escapes chain`);
      for(const text of item.text) assert(text.left>=item.anchor.left-1 && text.right<=item.anchor.right+1,
        `${slug}/${width}: long function/path text escapes its card`);
      if(index) {
        assert(item.arrow.includes('→') || item.explicit,`${slug}: missing arrow between function nodes`);
        if(Math.abs(item.top-chain.items[index-1].top)<2) horizontal=true;
      }
    }
    if(width<700 && chain.items.length>=4) {
      assert(new Set(chain.items.map(item=>Math.round(item.top))).size>1,`${slug}: narrow chain must wrap`);
    }
  }
  if(width>=1000) assert(horizontal,`${slug}/${width}: source paths regressed to vertical lists`);
  const source=page.locator('[data-vbr-callpaths], [data-execution-sources]').first();
  assert(await source.evaluate(n=>n.scrollHeight<=n.clientHeight+1),'source view must not hide later contexts in an internal scroller');
  assert.equal(await page.locator('.walkthrough-inspector a').count(),0);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
}

(async()=>{
  const browser=await chromium.launch({args:['--no-sandbox']});
  const context=await browser.newContext({reducedMotion:'reduce'});
  await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  try {
    for(const viewport of [{width:1440,height:900},{width:1920,height:1080},{width:390,height:844}]) {
      await page.setViewportSize(viewport);
      for(const item of pages) {
        const name=item.name || item.slug;
        await page.goto(`${base}/mechanisms/${item.slug}.html${item.query}`);
        await page.locator(`[data-${item.prefix}-next]`).waitFor({state:'visible'});
        // Match the reported end-of-request screenshot, not only the smaller initial state.
        await page.evaluate(prefix=>{
          const next=document.querySelector(`[data-${prefix}-next]`);
          let clicks=0;while(!next.disabled && clicks++<150)next.click();
          if(!next.disabled)throw new Error('Playback did not finish');
        },item.prefix);
        await alignOverview(page,item,false);
        const normal=await assertOverview(page,item,viewport,false);
        await page.screenshot({path:path.join(shots,`${name}-${viewport.width}-overview.png`)});
        await page.locator('[data-walkthrough-expand]').first().click();
        await page.waitForFunction(()=>document.querySelector('[data-walkthrough-expand]').getAttribute('aria-pressed')==='true');
        await alignOverview(page,item,true);
        const expanded=await assertOverview(page,item,viewport,true);
        await page.screenshot({path:path.join(shots,`${name}-${viewport.width}-fullscreen.png`)});
        await page.locator('[data-walkthrough-expand]').first().click();
        await page.waitForFunction(()=>document.querySelector('[data-walkthrough-expand]').getAttribute('aria-pressed')==='false');
        await showView(page,item,item.detail);
        await assertDetail(page,item,viewport);
        await showView(page,item,'source');
        await assertSources(page,item.slug,viewport.width);
        await page.locator('.walkthrough-callpath').first().evaluate(n=>window.scrollTo(0,
          window.scrollY+n.getBoundingClientRect().top-(innerWidth<700?220:80)));
        await page.screenshot({path:path.join(shots,`${name}-${viewport.width}-source.png`)});
        console.log(`PASS ${name} ${viewport.width}x${viewport.height}: overview ${Math.round(normal.height)}px / fullscreen ${Math.round(expanded.height)}px, full queue detail and wrapping source arrows`);
      }
      await page.goto(`${base}/mechanisms/cpu-execution.html?scenario=guest-syscall&view=source&step=return`);
      await assertSources(page,'cpu-execution',viewport.width);
    }
    assert.deepEqual(errors,[]);
    console.log(`PASS density, source arrows and responsive screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
