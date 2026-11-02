/* Local-only regression for compact, source-linked horizontal function paths. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const screenshots = process.env.SOURCE_PRESENTATION_SCREENSHOT_DIR;
const routes = {
  'network-io':'network-io', 'cpu-execution':'cpu-execution',
  'memory-access':'memory-access', 'interrupt-delivery':'interrupt-delivery',
  'gpu-execution':'gpu-execution', 'vm-lifecycle':'libvirt-domain-start',
};
const implementation = source => !['spec','specification','reference','hardware'].includes(source.kind)
  && (['function','assembly'].includes(source.kind)
    || Boolean(source.symbol && /\.(?:c|h|S|s|cc|cpp)$/.test(source.path || '')));

async function check(page, job, width) {
  const root = page.locator(job.selector);
  await root.waitFor({state:'visible'});
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const actual = await root.evaluate(node => ({
    links:[...node.querySelectorAll('[data-source-id]')].map(item => {
      const a=item.querySelector('a');
      return {id:item.dataset.sourceId, text:a.textContent, href:a.href,
        title:a.title, aria:a.getAttribute('aria-label'), active:item.dataset.current==='true'};
    }),
    groups:[...node.querySelectorAll('[data-callpath]')].map(group => {
      const list=group.querySelector('ol'), bounds=list.getBoundingClientRect();
      return {id:group.dataset.callpath, flow:group.dataset.flow,
        mode:group.querySelector('.walkthrough-call-mode')?.textContent,
        entry:group.querySelector('[data-entry-kind]')?.dataset.entryKind,
        entryText:group.querySelector('[data-entry-kind]')?.textContent,
        arrows:[...group.querySelectorAll('.walkthrough-call-arrow')].map(a => ({
          kind:a.dataset.relation, name:a.getAttribute('aria-label'), glyph:a.textContent})),
        display:getComputedStyle(list).display, wrap:getComputedStyle(list).flexWrap,
        left:bounds.left, right:bounds.right,
        items:[...list.children].map(item => {
          const b=item.getBoundingClientRect(), a=item.querySelector('a'), ab=a.getBoundingClientRect();
          const range=document.createRange(); range.selectNodeContents(a);
          return {id:item.dataset.sourceId, left:b.left, right:b.right, top:b.top,
            text:[...range.getClientRects()].filter(r=>r.width>0).map(r=>({left:r.left,right:r.right})),
            anchor:{left:ab.left,right:ab.right}};
        })};
    }),
    clipped:node.scrollHeight>node.clientHeight+1,
    overflow:document.documentElement.scrollWidth>innerWidth+1,
  }));
  const tag=`${job.name}/${width}`;
  assert(!actual.overflow,`${tag}: page overflow`);
  assert(!actual.clipped,`${tag}: source paths must not use an inner scroller`);
  assert.equal(await page.locator('.walkthrough-inspector a').count(),0,`${tag}: duplicate inspector sources`);
  assert(actual.links.length,`${tag}: references remain available`);
  for(const link of actual.links) {
    const source=job.sources[link.id], name=source.symbol || source.label;
    assert.equal(link.text,name,`${tag}/${link.id}: card must show only the function/reference name`);
    assert.equal(link.href,source.url,`${tag}/${link.id}: pinned source URL changed`);
    assert(link.aria.includes(name),`${tag}/${link.id}: accessible function name`);
    if(source.path) {
      assert(link.title.includes(source.path),`${tag}/${link.id}: hover retains file path`);
      assert(link.aria.includes(source.path),`${tag}/${link.id}: accessible file path`);
    }
  }
  const expectedActive=[...new Set(job.last.sourceIds)].sort();
  assert.deepEqual([...new Set(actual.links.filter(a=>a.active).map(a=>a.id))].sort(),expectedActive,
    `${tag}: source highlights follow the selected step`);
  const groups=(job.scene.callPaths || []).filter(p=>p.sourceIds.some(id=>implementation(job.sources[id])));
  assert.deepEqual(actual.groups.map(g=>g.id),groups.map(g=>g.id),`${tag}: implementation groups`);
  let horizontal=false;
  for(const [i,group] of actual.groups.entries()) {
    const expected=groups[i], ids=expected.sourceIds.filter(id=>implementation(job.sources[id]));
    assert.equal(group.flow,expected.flow || 'sequence');
    assert.equal(group.mode,expected.flow === 'return' ? '返回路径' : undefined,
      `${tag}/${group.id}: do not repeat ordinary call/order labels`);
    assert.equal(group.entry,expected.entry?.kind);
    if(expected.entry) assert(group.entryText.includes(expected.entry.label));
    assert.equal(group.display,'flex'); assert.equal(group.wrap,'wrap');
    assert.deepEqual(group.items.map(item=>item.id),ids);
    assert.equal(group.arrows.length,Math.max(0,ids.length-1));
    for(const [j,arrow] of group.arrows.entries()) {
      const relation=expected.relations?.find(r=>r.from===ids[j] && r.to===ids[j+1])?.kind
        || expected.flow || 'sequence';
      assert.equal(arrow.kind,relation,`${tag}/${group.id}: explicit edge semantics`);
      assert(arrow.name,`${tag}/${group.id}: accessible relationship`);
      assert.equal(arrow.glyph,'→',`${tag}/${group.id}: arrows must not repeat call captions`);
    }
    for(const [j,item] of group.items.entries()) {
      assert(item.left>=group.left-1 && item.right<=group.right+1,`${tag}/${group.id}: card escapes path`);
      for(const text of item.text) assert(text.left>=item.anchor.left-1 && text.right<=item.anchor.right+1,
        `${tag}/${item.id}: long symbol escapes card`);
      if(j && Math.abs(item.top-group.items[j-1].top)<2) horizontal=true;
    }
  }
  if(width>=1000 && groups.some(p=>p.sourceIds.filter(id=>implementation(job.sources[id])).length>=3)) {
    assert(horizontal,`${tag}: function chains should not become vertical lists`);
  }
  if(screenshots && ['local-blk-read','qemu-rx','guest-syscall','mdev-copy'].includes(job.name)) {
    await root.evaluate(node=>window.scrollTo(0,window.scrollY+node.getBoundingClientRect().top-100));
    await page.screenshot({path:path.join(screenshots,`${job.name}-${width}.png`)});
  }
}

(async()=>{
  if(screenshots) fs.mkdirSync(screenshots,{recursive:true});
  const browser=await chromium.launch({args:['--no-sandbox']});
  const context=await browser.newContext({reducedMotion:'reduce'});
  await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
  const page=await context.newPage(), errors=[], jobs=[];
  page.on('pageerror',error=>errors.push(error.message));
  try {
    const disk=`${base}/mechanisms/virtio-blk-read.html`;
    await page.goto(disk);
    const catalog=JSON.parse(await page.locator('#block-io-scenarios-data').textContent());
    for(const route of catalog.routes) for(const operation of ['read','write']) {
      const scene=route.scenarios[operation], last=scene.steps.at(-1);
      jobs.push({name:`${route.id}-${operation}`, scene, last, sources:scene.sources,
        selector:'[data-vbr-callpaths]',
        url:`${disk}?backend=${route.id}&operation=${operation}&view=source&step=${last.id}`});
    }
    for(const slug of Object.values(routes)) {
      const url=`${base}/mechanisms/${slug}.html`;
      await page.goto(url);
      const demo=JSON.parse(await page.locator('#execution-demo-data').textContent());
      for(const scene of demo.scenarios) {
        const last=scene.steps.at(-1);
        jobs.push({name:scene.id, scene, last, sources:demo.sources,
          selector:'[data-execution-sources]', url:`${url}?scenario=${scene.id}&view=source&step=${last.id}`});
      }
    }
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:900});
      for(const job of jobs) {await page.goto(job.url); await check(page,job,width);}
      console.log(`PASS ${width}px: ${jobs.length} source views, compact labels, pinned links, relation semantics, wrapping and highlights`);
    }
    assert.deepEqual(errors,[],'browser errors');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
