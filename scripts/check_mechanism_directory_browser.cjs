/* Local-only checks for progressive topic disclosure and navigable deep links. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const url = `${base}/mechanisms.html`;
const shots = process.env.MECHANISM_DIRECTORY_SCREENSHOT_DIR || '/tmp/mechanism-directory-review';
const catalogue = JSON.parse(fs.readFileSync(path.join(__dirname, '../virt_report/content/mechanisms.json'), 'utf8'));
const published = catalogue.topics.map(topic => ({...topic,
  details: catalogue.articles.filter(article => article.topic === topic.slug && article.slug !== topic.overview),
})).filter(topic => catalogue.articles.some(article => article.topic === topic.slug));
const detailCount = published.reduce((sum, topic) => sum + topic.details.length, 0);
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
  const architectureOrder = ['virtualization-basics', 'libvirt-internals', 'qemu-internals', 'kvm-internals'];
  assert.deepEqual(await page.locator('.mechanism-directory-row').first().locator('a').evaluateAll(nodes =>
    nodes.map(node => node.getAttribute('href'))),
    ['#virtualization-basics', '#libvirt-internals', '#qemu-internals', '#kvm-internals'],
    'all component entries navigate within the same catalogue');
  assert.deepEqual(await page.locator('.mechanism-group').first().locator('.mechanism-topic').evaluateAll(nodes =>
    nodes.map(node => node.id)), architectureOrder, 'body and navigation keep the same reading order');
  assert.deepEqual(await page.locator('.mechanism-group').last().locator('.mechanism-topic').evaluateAll(nodes =>
    nodes.map(node => node.id)), ['linux-foundations'], 'Linux foundations remain last');
  const chipCounts = await page.locator('.mechanism-directory a').evaluateAll(nodes =>
    Object.fromEntries(nodes.map(node => [node.getAttribute('href').slice(1),
      Number.parseInt(node.querySelector('.mechanism-directory-count')?.textContent,10)])));
  assert.deepEqual(chipCounts, Object.fromEntries(published.map(item => [item.slug,
    catalogue.articles.filter(article => article.topic===item.slug).length])),
    'directory counts include each published article once, including the overview');
  assert.equal(await page.locator('a[href="#block-operations"],a[href="mechanisms/qemu-virtio-mem.html"],a[href="mechanisms/qemu-live-block-operations.html"]').count(),0);
  assert.equal(chipCounts['kvm-internals'],9);
  assert.equal(chipCounts['cpu-virtualization'],2);
  assert.equal(chipCounts['memory-virtualization'],2);
  const buttons=page.locator('[data-mechanism-toggle]');
  assert.equal(await buttons.count(),published.filter(topic => topic.details.length).length,
    'only topics with detail articles get a toggle');
  assert.equal(await page.locator('.mechanism-topic h3 a').count(),published.filter(item=>item.overview).length,
    'topic headings link to their overview articles');
  assert.equal(await page.locator('a[href="kvm/"],a[href="mechanisms/cpu-model-migration.html"],a[href="mechanisms/qemu-qcow2.html"]').count(),0);
  assert.equal(await topic(page,'qemu-internals').locator('a[href="mechanisms/qemu-tcg.html"]').count(),1);
  assert.equal(await topic(page,'cpu-virtualization').locator('a[href="mechanisms/qemu-tcg.html"]').count(),0);
  assert.equal(await topic(page,'live-upgrade').locator('h3 a').getAttribute('href'),
    'mechanisms/live-upgrade-overview.html', 'live upgrade has a cross-layer overview');
  assert.equal(await page.locator('.mechanism-list-item:visible').count(),0,'detail lists initially collapsed');
  assert.equal(await page.locator('.mechanism-topic-header p:visible').count(),published.length,'topic summaries remain visible');
  for(const id of ['virtualization-basics', 'interrupt-virtualization']) {
    assert.equal(await toggle(page,id).count(),0,`${id}: no empty or demo disclosure`);
    assert(await topic(page,id).locator('h3 a').isVisible());
  }
  for(const id of ['block-io-walkthrough', 'network-io-walkthrough', 'cpu-execution-walkthrough',
    'memory-access-walkthrough', 'interrupt-delivery-walkthrough', 'gpu-execution-walkthrough',
    'virtio-gpu-walkthrough']) {
    assert.equal(await topic(page,id).count(),0,`${id}: demos are part of their subject`);
    assert.equal(await page.locator(`.mechanism-topic-alias[id="${id}"]`).count(),1,`${id}: old bookmarks survive`);
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
    const brand=resolve('var(--brand)'), ink=resolve('var(--ink)'), muted=resolve('var(--muted)');probe.remove();
    return {brand,ink,muted,
      headings:[...document.querySelectorAll('.mechanism-topic h3 a,.mechanism-list-item :is(h4,h5) a')]
      .map(a=>getComputedStyle(a).color),chip:getComputedStyle(document.querySelector('.mechanism-directory a')).color};
  });
  assert(colors.headings.every(color=>color===colors.brand),'article/overview titles use the site link color');
  assert.equal(colors.chip,colors.ink,'directory chips retain their neutral style');
  await responsive(page,width);
  await page.screenshot({path:path.join(shots,`directory-${width}-collapsed.png`)});

  await nav(page,'kvm-internals').click(); await expanded(page,'kvm-internals');
  const kvmTopic=topic(page,'kvm-internals');
  assert.deepEqual(await kvmTopic.locator('.mechanism-cluster > h4').allTextContents(),
    ['执行与协作','内存管理'], 'KVM implementation articles are grouped within the shared catalogue');
  assert.deepEqual(await kvmTopic.locator('.mechanism-list-item a').evaluateAll(nodes =>
    nodes.map(node => node.getAttribute('href'))), [
    'mechanisms/kvm-vm-creation.html','mechanisms/kvm-vcpu-requests.html','mechanisms/kvm-vcpu-halt.html',
    'mechanisms/kvm-virtual-time.html','mechanisms/kvm-nested-vmx.html',
    'mechanisms/kvm-ept-fault.html','mechanisms/kvm-hugepages.html','mechanisms/kvm-dirty-ring.html',
  ]);
  await kvmTopic.screenshot({path:path.join(shots,`directory-${width}-kvm.png`)});
  await toggle(page,'kvm-internals').click(); await expanded(page,'kvm-internals',false);

  await toggle(page,'linux-foundations').click(); await expanded(page,'linux-foundations');
  assert.equal(await topic(page,'linux-foundations').locator('h3 a').getAttribute('href'),
    'mechanisms/linux-kernel-evolution.html', 'Linux foundations has a real overview');
  assert.equal(await list(page,'linux-foundations').locator('.mechanism-list-item').count(), 6);
  assert(await topic(page,'linux-foundations').evaluate(node => {
    const left=node.closest('.mechanism-group').querySelector('h2').getBoundingClientRect().left;
    return [...node.querySelectorAll('h3,h4,p')].every(el =>
      Math.abs(el.getBoundingClientRect().left-left)<1);
  }), 'Linux category, article titles and summaries share the group’s left edge');
  const summaries = await list(page,'linux-foundations').locator('.mechanism-list-item > p').evaluateAll(nodes =>
    nodes.map(node => ({height:node.getBoundingClientRect().height, line:parseFloat(getComputedStyle(node).lineHeight),
      width:node.getBoundingClientRect().width, available:node.parentElement.getBoundingClientRect().width})));
  assert(summaries.every(p => Math.abs(p.width-p.available) < 1), 'summaries use full available width');
  if (width >= 1280) assert(summaries.every(p => p.height <= p.line + 1), 'short desktop summaries fit on one line');
  await topic(page,'linux-foundations').screenshot({path:path.join(shots,`directory-${width}-linux.png`)});
  await toggle(page,'linux-foundations').click(); await expanded(page,'linux-foundations',false);

  await toggle(page,'device-virtualization').click(); await expanded(page,'device-virtualization');
  await toggle(page,'memory-virtualization').click(); await expanded(page,'memory-virtualization');
  const titleStyles = await page.locator('.mechanism-list-item :is(h4,h5)').evaluateAll(nodes => nodes.map(node => {
    const style = getComputedStyle(node);
    return [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight, style.marginBottom];
  }));
  assert(titleStyles.length > 0);
  assert(titleStyles.every(style => JSON.stringify(style) === JSON.stringify(titleStyles[0])),
    'clustered h5 titles and ordinary h4 titles share font, weight, size, line height and spacing');
  const cluster = topic(page,'device-virtualization').locator('.mechanism-cluster').first();
  assert.equal(await cluster.locator('h5').count(), 2, 'both Virtio and Virtqueue titles are covered');
  const hierarchy = await cluster.evaluate(node => {
    const heading = node.querySelector('h4');
    const article = node.querySelector('h5');
    const topic = node.closest('.mechanism-topic').querySelector('h3');
    const group = node.closest('.mechanism-group').querySelector('h2');
    return {
      sizes: [group,topic,heading,article].map(el => parseFloat(getComputedStyle(el).fontSize)),
      lefts: [group,topic,heading,article].map(el => el.getBoundingClientRect().left),
      decorations: [group,topic,node,heading,article].map(el => ({
        border: parseFloat(getComputedStyle(el).borderLeftWidth), shadow: getComputedStyle(el).boxShadow,
      })),
    };
  });
  assert(hierarchy.sizes.every((size,index,array) => index === 0 || size < array[index-1]),
    'group, topic, cluster and article titles have a descending visual hierarchy');
  assert(hierarchy.lefts.every(left => Math.abs(left-hierarchy.lefts[0])<1),
    'group, topic, cluster and article titles align without indentation');
  assert(hierarchy.decorations.every(style => style.border===0 && style.shadow==='none'),
    'heading hierarchy does not add vertical bars or shadows');
  await cluster.screenshot({path:path.join(shots,`directory-${width}-virtio-fonts.png`)});
  await topic(page,'device-virtualization').locator('h3').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(shots,`directory-${width}-hierarchy.png`)});
  await responsive(page,width);
  await toggle(page,'device-virtualization').click(); await expanded(page,'device-virtualization',false);
  await toggle(page,'memory-virtualization').click(); await expanded(page,'memory-virtualization',false);

  await toggle(page,'live-upgrade').click(); await expanded(page,'live-upgrade');
  assert.equal(await list(page,'live-upgrade').locator('.mechanism-list-item').count(),2);
  assert.equal(await list(page,'live-upgrade').locator('a[href="mechanisms/qemu-cpr-transfer.html"]').textContent(),
    'QEMU 热升级：CPR 的资源交接与状态恢复');
  assert(await list(page,'live-upgrade').locator('a[href="mechanisms/kvm-live-upgrade.html"]').isVisible());
  await topic(page,'live-upgrade').screenshot({path:path.join(shots,`directory-${width}-live-upgrade.png`)});
  await list(page,'live-upgrade').locator('a[href="mechanisms/qemu-cpr-transfer.html"]').click();
  await page.waitForURL(`${base}/mechanisms/qemu-cpr-transfer.html`);
  await page.goto(`${url}#live-upgrade`); await ready(page); await expanded(page,'live-upgrade');
  await toggle(page,'live-upgrade').click(); await expanded(page,'live-upgrade',false);

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
  await expanded(page,'device-virtualization'); await positioned(page,'device-virtualization');
  await page.screenshot({path:path.join(shots,`directory-${width}-walkthroughs.png`)});
  await page.goto(`${url}#%E0%A4%A`); await ready(page);
  await responsive(page,width);
  console.log(`PASS ${width}px: default disclosure, keyboard, links, hashes/alias/history, colors and layout`);
}

(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await launchBrowser({args:['--no-sandbox']});
  const errors=[];
  try {
    const context=await browser.newContext({reducedMotion:'reduce'});
    await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    for(const width of [1440,390,320]) await check(page,width);
    const nojs=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:900}});
    await nojs.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
    const plain=await nojs.newPage();await plain.goto(url);
    assert.equal(await plain.locator('.mechanism-list-item:visible').count(),detailCount,
      'all detail articles remain readable without JavaScript');
    assert.equal(await plain.locator('[data-mechanism-toggle]:visible').count(),0,
      'inert controls remain hidden without JavaScript');
    assert(await plain.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[],'no browser errors');
    console.log(`PASS no-JavaScript fallback; screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
