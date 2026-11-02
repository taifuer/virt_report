/* Local-only layout checks for the content-adjacent back-to-top and Guest mode labels. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.BACK_TOP_BADGE_SCREENSHOT_DIR || '/tmp/backtop-badge-review';
const disk = `${base}/mechanisms/virtio-blk-read.html`;
const settle = page => page.evaluate(() => new Promise(resolve =>
  requestAnimationFrame(() => requestAnimationFrame(resolve))));

async function backTop(page, width) {
  await page.setViewportSize({width,height:900});
  await page.goto(disk); await page.locator('[data-vbr-lab][data-initialized="true"]').waitFor();
  const button=page.locator('[data-back-to-top]');
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'})); await settle(page);
  assert(!(await button.evaluate(n=>n.classList.contains('is-visible'))),'not visible before scrolling');
  await page.evaluate(()=>window.scrollTo({top:1200,behavior:'instant'})); await settle(page);
  assert(await button.isVisible(),'button appears after meaningful scrolling');
  const bounds=await button.evaluate(node=>{
    const b=node.getBoundingClientRect(),w=document.querySelector('main>.wrap').getBoundingClientRect();
    return {left:b.left,right:b.right,bottom:b.bottom,width:b.width,height:b.height,contentRight:w.right,
      viewport:innerWidth,viewportHeight:innerHeight};
  });
  assert.equal(bounds.width,44); assert.equal(bounds.height,44);
  assert(Math.abs(bounds.viewportHeight-bounds.bottom-(width<=700?60:80))<2,
    `${width}: baseline bottom offset ${JSON.stringify(bounds)}`);
  const gap=bounds.left-bounds.contentRight;
  if(width>=1340) assert(gap>=16 && gap<=24,`${width}: button should hug the content gutter (${gap})`);
  else assert(Math.abs(bounds.viewport-bounds.right-16)<2,`${width}: narrow-screen right inset`);
  assert(bounds.left>=0 && bounds.right<=width,`${width}: button must remain inside viewport`);
  await page.screenshot({path:path.join(shots,`backtop-${width}.png`)});
  await page.evaluate(()=>window.scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'}));
  await settle(page);
  const footer=await button.evaluate(node=>({bottom:node.getBoundingClientRect().bottom,
    top:document.querySelector('.site-foot').getBoundingClientRect().top}));
  assert(footer.bottom<=footer.top-15,`${width}: button overlaps footer ${JSON.stringify(footer)}`);
  await button.click(); await settle(page);
  assert(await page.evaluate(()=>scrollY<2),'back-to-top returns to the page beginning');
  assert(!(await button.evaluate(n=>n.classList.contains('is-visible'))),'button hides again at the top');
  console.log(`PASS ${width}px back-to-top: content gutter, height, footer and activation`);
}

async function labels(page, tag, requireSingleRow) {
  const result=await page.locator('[data-vbr-node="application"], [data-vbr-node="driver"]').evaluateAll(nodes=>
    nodes.map(node=>{
      const label=node.querySelector('.vbr-mode-tag'),title=node.querySelector('[data-vbr-app-title], [data-vbr-driver-title]');
      const b=node.getBoundingClientRect(),l=label.getBoundingClientRect(),t=title.getBoundingClientRect();
      const rect=r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height});
      const textRects=el=>{const range=document.createRange();range.selectNodeContents(el);
        return [...range.getClientRects()].filter(r=>r.width>0).map(rect);};
      return {node:node.dataset.vbrNode,text:label.textContent,title:title.textContent,
        box:rect(b),label:rect(l),titleBox:rect(t),texts:textRects(label).concat(textRects(title)),
        titleLines:textRects(title).length,active:node.dataset.active};
    }));
  assert.equal(result.length,2,`${tag}: both Guest execution contexts remain represented`);
  for(const item of result) {
    const {box,label,titleBox}=item;
    for(const r of item.texts) assert(r.left>=box.left-1 && r.right<=box.right+1 && r.top>=box.top-1 && r.bottom<=box.bottom+1,
      `${tag}/${item.node}: label/title escapes card ${JSON.stringify(item)}`);
    const overlap=Math.min(label.right,titleBox.right)-Math.max(label.left,titleBox.left)>1
      && Math.min(label.bottom,titleBox.bottom)-Math.max(label.top,titleBox.top)>1;
    assert(!overlap,`${tag}/${item.node}: mode label overlays title`);
    assert(label.right>=titleBox.right-1,`${tag}/${item.node}: label belongs at the right edge`);
    if(requireSingleRow) {
      assert.equal(item.titleLines,1,`${tag}/${item.node}: normal desktop title fits on one line`);
      assert(label.top<titleBox.bottom && label.bottom>titleBox.top,
        `${tag}/${item.node}: ample-width card should use one title row`);
    }
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${tag}: page overflow`);
  return result;
}

async function badgeScenarios(page,width) {
  await page.setViewportSize({width,height:1000});
  const selections=width===1440
    ? ['local-blk','local-scsi','rbd-blk','spdk-blk','spdk-scsi'].flatMap(backend=>['read','write'].map(operation=>({backend,operation})))
    : [{backend:'local-blk',operation:'read'},{backend:'local-scsi',operation:'write'},{backend:'spdk-scsi',operation:'write'}];
  selections.push({backend:'local-scsi',operation:'write',completion:'fua'});
  for(const scene of selections) {
    const name=`${scene.backend}-${scene.operation}-${scene.completion || 'ordinary'}`;
    await page.goto(`${disk}?${new URLSearchParams(scene)}`);
    await page.locator('[data-vbr-lab][data-initialized="true"]').waitFor();
    await settle(page);
    const result=await labels(page,`${name}/${width}/normal`,width>=1280 && scene.completion!=='fua');
    assert.equal(result[0].text,scene.completion==='fua'?'请求边界':'用户态');
    assert.equal(result[1].text,'内核态');
    await page.evaluate(()=>{const next=document.querySelector('[data-vbr-next]');
      for(let i=0;i<100 && !next.disabled;i++)next.click();});
    await settle(page); await labels(page,`${name}/${width}/finished`,false);
    const expand=page.locator('[data-walkthrough-expand]').first();
    await expand.click();
    await page.waitForFunction(()=>document.querySelector('[data-walkthrough-expand]').getAttribute('aria-pressed')==='true');
    await settle(page);
    await labels(page,`${name}/${width}/expanded`,width>=1280 && scene.completion!=='fua');
    const covered=await page.locator('[data-back-to-top]').evaluate(node=>{
      const r=node.getBoundingClientRect(),front=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      return !front || !node.contains(front);
    });
    assert(covered,'global back-to-top must not cover the expanded walkthrough');
    if(scene.backend==='local-blk' || scene.completion==='fua') {
      await page.locator('[data-vbr-board]').evaluate(node=>{
        const lab=node.closest('[data-walkthrough-lab]');
        const toolbarHeight=lab.querySelector('.walkthrough-toolbar').getBoundingClientRect().height;
        lab.scrollTop+=node.getBoundingClientRect().top-lab.getBoundingClientRect().top-toolbarHeight-24;
      });
      await settle(page);
      await page.screenshot({path:path.join(shots,`badge-${name}-${width}.png`)});
    }
    await expand.click(); await settle(page);
  }
  console.log(`PASS ${width}px Guest labels: ${selections.length} scenarios, initial/final and fullscreen`);
}

(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({args:['--no-sandbox']});
  const context=await browser.newContext({reducedMotion:'reduce'});
  await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
  const page=await context.newPage(),errors=[]; page.on('pageerror',error=>errors.push(error.message));
  try {
    for(const width of [320,390,1280,1440,1920,2560]) await backTop(page,width);
    // Resize an existing scrolled page, rather than testing only initial media-query selection.
    await page.evaluate(()=>window.scrollTo({top:1200,behavior:'instant'}));
    await page.setViewportSize({width:390,height:900}); await settle(page);
    const offset=await page.locator('[data-back-to-top]').evaluate(n=>innerHeight-n.getBoundingClientRect().bottom);
    assert(Math.abs(offset-60)<2,'resize updates mobile button offset without reload');
    for(const width of [320,390,1280,1440,1920]) await badgeScenarios(page,width);
    assert.deepEqual(errors,[],'no browser errors');
    console.log(`PASS back-to-top and Guest-label layout; screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
