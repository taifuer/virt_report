/* Local-only checks for progressive topic disclosure and navigable deep links. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const url = `${base}/mechanisms.html`;
const shots = process.env.MECHANISM_DIRECTORY_SCREENSHOT_DIR || '/tmp/mechanism-directory-review';
const topic = (page, id) => page.locator(`.mechanism-topic[id="${id}"]`);
const toggle = (page, id) => topic(page,id).locator('[data-mechanism-toggle]');
const list = (page, id) => topic(page,id).locator('.mechanism-list');
const nav = (page, id) => page.locator(`.mechanism-directory a[href="#${id}"]`);
const settle = page => page.evaluate(() => new Promise(resolve =>
  requestAnimationFrame(() => requestAnimationFrame(resolve))));

async function ready(page) {
  await page.locator('[data-directory-initialized="true"]').waitFor();
  await settle(page);
}

async function expanded(page, id, value = true) {
  await page.waitForFunction(({id,value}) => {
    const button=document.getElementById(id).querySelector('[data-mechanism-toggle]');
    return button.getAttribute('aria-expanded') === String(value);
  }, {id,value});
  assert.equal(await list(page,id).isVisible(),value,`${id}: disclosure state and visibility agree`);
}

async function positioned(page, id) {
  await settle(page);
  const bounds=await topic(page,id).evaluate(node=>({
    top:node.querySelector('h3').getBoundingClientRect().top,
    bottom:node.querySelector('h3').getBoundingClientRect().bottom,
    header:document.querySelector('.site-head').getBoundingClientRect().bottom,
    height:innerHeight,
  }));
  assert(bounds.top>=bounds.header-1 && bounds.bottom<bounds.height,
    `${id}: anchor heading should be visible below the sticky header ${JSON.stringify(bounds)}`);
}

async function responsive(page, width) {
  const layout=await page.evaluate(()=>({
    overflow:document.documentElement.scrollWidth>innerWidth+1,
    controls:[...document.querySelectorAll('[data-mechanism-toggle]')].map(button=>{
      const r=button.getBoundingClientRect();
      const heading=button.parentElement.querySelector('h3').getBoundingClientRect();
      return {width:r.width,height:r.height,left:r.left,right:r.right,
        gap:r.left-heading.right,text:button.innerText.trim(),label:button.getAttribute('aria-label')};
    }),
  }));
  assert(!layout.overflow,`${width}: page must not overflow horizontally`);
  for(const control of layout.controls) {
    assert(control.height>=44 && control.width>=44,`${width}: toggle touch target`);
    assert(control.left>=0 && control.right<=width,`${width}: toggle stays in viewport`);
    assert(control.gap>=-1 && control.gap<=16,`${width}: icon remains beside its heading`);
    assert.equal(control.text,'',`${width}: disclosure is icon-only, without article-count captions`);
    assert(control.label,`${width}: icon-only control retains its accessible name`);
  }
}

async function check(page,width) {
  await page.setViewportSize({width,height:900});
  await page.goto(url); await ready(page);
  const buttons=page.locator('[data-mechanism-toggle]');
  assert.equal(await buttons.count(),10,'only topics with detail articles get a toggle');
  assert.equal(await page.locator('.mechanism-topic h3 a').count(),19,'all overview links remain available');
  assert.equal(await page.locator('.mechanism-list-item:visible').count(),0,'detail lists initially collapsed');
  assert.equal(await page.locator('.mechanism-topic-header p:visible').count(),19,'topic summaries remain visible');
  for(const id of ['virtualization-basics','live-upgrade','block-io-walkthrough','network-io-walkthrough',
    'cpu-execution-walkthrough','memory-access-walkthrough','interrupt-delivery-walkthrough','gpu-execution-walkthrough',
    'virtio-gpu-walkthrough']) {
    assert.equal(await toggle(page,id).count(),0,`${id}: no empty or demo disclosure`);
    assert(await topic(page,id).locator('h3 a').isVisible());
  }
  for(const item of await buttons.evaluateAll(nodes=>nodes.map(button=>({
    expanded:button.getAttribute('aria-expanded'),controls:button.getAttribute('aria-controls'),
    hidden:document.getElementById(button.getAttribute('aria-controls'))?.hidden,
  })))) {
    assert.equal(item.expanded,'false'); assert(item.controls && item.hidden);
  }
  const colors=await page.evaluate(()=>{
    const probe=document.createElement('i');document.body.append(probe);
    const resolve=value=>{probe.style.color=value;return getComputedStyle(probe).color;};
    const brand=resolve('var(--brand)'), ink=resolve('var(--ink)');probe.remove();
    return {brand,ink,headings:[...document.querySelectorAll('.mechanism-topic h3 a,.mechanism-list-item h4 a')]
      .map(a=>getComputedStyle(a).color),chip:getComputedStyle(document.querySelector('.mechanism-directory a')).color};
  });
  assert(colors.headings.every(color=>color===colors.brand),'article/overview titles use the site link color');
  assert.equal(colors.chip,colors.ink,'directory chips retain their neutral style');
  await responsive(page,width);
  await page.screenshot({path:path.join(shots,`directory-${width}-collapsed.png`)});

  await toggle(page,'cpu-virtualization').focus();
  await page.keyboard.press('Enter'); await expanded(page,'cpu-virtualization');
  assert(await toggle(page,'cpu-virtualization').evaluate(button=>button===document.activeElement),
    'keyboard toggle retains focus');
  await page.keyboard.press('Space'); await expanded(page,'cpu-virtualization',false);
  assert.equal(await topic(page,'cpu-virtualization').locator('h3 a').getAttribute('href'),
    'mechanisms/cpu-virtualization.html','title remains a real overview link, not the toggle');

  await nav(page,'cpu-virtualization').click(); await expanded(page,'cpu-virtualization');
  await positioned(page,'cpu-virtualization');
  await page.screenshot({path:path.join(shots,`directory-${width}-expanded.png`)});
  await toggle(page,'cpu-virtualization').click(); await expanded(page,'cpu-virtualization',false);
  await nav(page,'memory-virtualization').click(); await expanded(page,'memory-virtualization');
  await page.goBack(); await expanded(page,'cpu-virtualization'); await positioned(page,'cpu-virtualization');
  await expanded(page,'memory-virtualization');
  await toggle(page,'memory-virtualization').click(); await expanded(page,'memory-virtualization',false);
  await page.goForward(); await expanded(page,'memory-virtualization'); await positioned(page,'memory-virtualization');
  await toggle(page,'memory-virtualization').click(); await expanded(page,'memory-virtualization',false);
  await nav(page,'memory-virtualization').click(); await expanded(page,'memory-virtualization');
  await positioned(page,'memory-virtualization');
  await responsive(page,width);

  await page.goto(`${url}#io-virtualization`); await ready(page);
  await expanded(page,'device-virtualization'); await positioned(page,'device-virtualization');
  await page.goto(`${url}#cpu-virtualization`); await ready(page);
  await expanded(page,'cpu-virtualization'); await positioned(page,'cpu-virtualization');
  await page.reload(); await ready(page); await expanded(page,'cpu-virtualization');
  await topic(page,'cpu-virtualization').locator('h3 a').click();
  await page.waitForURL(`${base}/mechanisms/cpu-virtualization.html`);
  await page.goto(`${url}#block-io-walkthrough`); await ready(page);
  await positioned(page,'block-io-walkthrough');
  await page.screenshot({path:path.join(shots,`directory-${width}-walkthroughs.png`)});
  await page.goto(`${url}#%E0%A4%A`); await ready(page);
  await responsive(page,width);
  console.log(`PASS ${width}px: default disclosure, keyboard, links, hashes/alias/history, colors and layout`);
}

(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({args:['--no-sandbox']});
  const errors=[];
  try {
    const context=await browser.newContext({reducedMotion:'reduce'});
    await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    for(const width of [1440,390,320]) await check(page,width);
    const nojs=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:900}});
    await nojs.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
    const plain=await nojs.newPage();await plain.goto(url);
    assert.equal(await plain.locator('.mechanism-list-item:visible').count(),41,
      'all detail articles remain readable without JavaScript');
    assert.equal(await plain.locator('[data-mechanism-toggle]:visible').count(),0,
      'inert controls remain hidden without JavaScript');
    assert(await plain.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[],'no browser errors');
    console.log(`PASS no-JavaScript fallback; screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
