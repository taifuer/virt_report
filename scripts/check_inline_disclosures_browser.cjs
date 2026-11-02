/* Local-only layout and keyboard checks for title-adjacent disclosure controls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.INLINE_DISCLOSURES_SCREENSHOT_DIR || '/tmp/inline-disclosures-review';
const settle = page => page.evaluate(() => new Promise(resolve =>
  requestAnimationFrame(() => requestAnimationFrame(resolve))));

async function noOverflow(page, label) {
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${label}: horizontal overflow`);
}

async function transform(control, pseudo = null) {
  return control.evaluate((node,pseudo)=>getComputedStyle(node,pseudo).transform,pseudo);
}

async function shot(page, control, name, width) {
  // Focus behavior is asserted separately; screenshots show the resting layout.
  await page.evaluate(()=>document.activeElement?.blur());
  await page.mouse.move(0,0);
  await control.evaluate(node=>window.scrollTo({top:window.scrollY+node.getBoundingClientRect().top-115,behavior:'instant'}));
  await settle(page);
  await page.screenshot({path:path.join(shots,`${name}-${width}.png`)});
}

async function mechanisms(page,width) {
  await page.goto(`${base}/mechanisms.html`);
  await page.locator('[data-directory-initialized="true"]').waitFor();
  const button=page.locator('#cpu-virtualization [data-mechanism-toggle]');
  const list=page.locator('#mechanism-list-cpu-virtualization');
  const icon=button.locator('svg');
  assert.equal(await button.getAttribute('aria-expanded'),'false');
  assert.equal((await button.innerText()).trim(),'');
  assert(await button.getAttribute('aria-label'));
  const geometry=await button.evaluate(node=>{
    const b=node.getBoundingClientRect(),h=node.parentElement.querySelector('h3').getBoundingClientRect();
    return {gap:b.left-h.right,width:b.width,height:b.height};
  });
  assert(geometry.gap>=-1 && geometry.gap<=16,`mechanisms/${width}: detached icon ${JSON.stringify(geometry)}`);
  assert(geometry.width>=44 && geometry.height>=44,'icon retains an adequate touch target');
  const state=async()=>await icon.count()?transform(icon):transform(button,'::after');
  const closed=await state();
  await button.focus(); await page.keyboard.press('Enter'); await settle(page);
  assert.equal(await button.getAttribute('aria-expanded'),'true'); assert(await list.isVisible());
  assert.notEqual(await state(),closed,'mechanisms chevron indicates expanded state');
  await page.keyboard.press('Space'); await settle(page);
  assert.equal(await button.getAttribute('aria-expanded'),'false'); assert(await list.isHidden());
  assert(await button.evaluate(node=>node===document.activeElement));
  await button.click(); await settle(page); assert(await list.isVisible());
  assert.equal(await page.locator('#cpu-virtualization h3 a').getAttribute('href'),'mechanisms/cpu-virtualization.html');
  await noOverflow(page,`mechanisms/${width}`);
  await shot(page,button,'mechanisms-expanded',width);
}

async function about(page,width) {
  await page.goto(`${base}/about.html`);
  const details=page.locator('.about-updates'),summary=details.locator('summary');
  assert.equal(await details.getAttribute('open'),null,'updates remain initially collapsed');
  const layout=await summary.evaluate(node=>{
    const style=getComputedStyle(node),heading=node.querySelector('h2');
    return {justify:style.justifyContent,gap:parseFloat(style.columnGap),grow:getComputedStyle(heading).flexGrow,
      after:getComputedStyle(node,'::after').content};
  });
  assert(['start','flex-start','normal'].includes(layout.justify),'updates arrow must follow the title, not use space-between');
  assert.equal(layout.grow,'0','updates title must not push the arrow to the far edge');
  assert(layout.gap>=0 && layout.gap<=16 && layout.after!=='none');
  const closed=await transform(summary,'::after');
  await summary.focus(); await page.keyboard.press('Enter'); await settle(page);
  assert(await details.evaluate(node=>node.open)); assert(await details.locator('.update-list').isVisible());
  assert.notEqual(await transform(summary,'::after'),closed);
  await page.keyboard.press('Space'); await settle(page); assert(!(await details.evaluate(node=>node.open)));
  await noOverflow(page,`about/${width}`); await shot(page,summary,'about-collapsed',width);
  await summary.click(); await settle(page); assert(await details.evaluate(node=>node.open));
}

async function versions(page,width) {
  await page.goto(`${base}/versions.html`);
  const root=page.locator('[data-version-browser]');
  await page.waitForFunction(()=>document.querySelector('[data-version-view="compact"]').getAttribute('aria-pressed')==='true');
  const details=page.locator('.version-year:not([hidden]) [data-version-disclosure]').first();
  const summary=details.locator('summary'),icon=details.locator('.version-year-chevron');
  assert(await details.evaluate(node=>node.open),'year groups retain initial open state');
  const gap=await icon.evaluate(node=>{
    const text=node.parentElement.firstChild,range=document.createRange();range.selectNodeContents(text);
    return node.getBoundingClientRect().left-range.getBoundingClientRect().right;
  });
  assert(gap>=-1 && gap<=20,`versions/${width}: chevron must follow the year, before its count (${gap})`);
  const opened=await transform(icon);
  await summary.focus(); await page.keyboard.press('Enter'); await settle(page);
  assert(!(await details.evaluate(node=>node.open))); assert.notEqual(await transform(icon),opened);
  await page.keyboard.press('Space'); await settle(page); assert(await details.evaluate(node=>node.open));
  await noOverflow(page,`versions/${width}`); await shot(page,summary,'versions',width);
  await root.locator('[data-version-view="detailed"]').click(); await settle(page);
  const detailed=page.locator('.version-year:not([hidden]) [data-version-disclosure]').first();
  assert(await detailed.evaluate(node=>node.open),'detailed view retains expanded years');
  assert.equal(await detailed.locator('summary').getAttribute('aria-disabled'),'true');
}

async function topicsAndNavigation(page,width) {
  await page.goto(`${base}/topics.html`);
  const button=page.locator('[data-topic-group-toggle]').first();
  assert.equal(await button.getAttribute('aria-expanded'),'true');
  const inline=await button.evaluate(node=>({display:getComputedStyle(node).display,gap:parseFloat(getComputedStyle(node).columnGap)}));
  assert.equal(inline.display,'inline-flex'); assert(inline.gap<=16);
  await button.focus(); await page.keyboard.press('Enter'); await settle(page);
  assert.equal(await button.getAttribute('aria-expanded'),'false');
  await page.keyboard.press('Space'); await settle(page); assert.equal(await button.getAttribute('aria-expanded'),'true');
  if(width<700) {
    await page.locator('[data-site-menu-toggle]').click();
    const reports=page.locator('.nav-reports>summary');
    const gap=await reports.evaluate(node=>{
      const range=document.createRange();range.selectNodeContents(node.firstChild);
      return node.querySelector('svg').getBoundingClientRect().left-range.getBoundingClientRect().right;
    });
    assert(gap>=-1 && gap<=20,`mobile report dropdown icon follows its label (${gap})`);
  }
  await noOverflow(page,`topics/navigation/${width}`);
}

(async()=>{
  fs.mkdirSync(shots,{recursive:true});
  const browser=await chromium.launch({args:['--no-sandbox']});
  const errors=[];
  try {
    const context=await browser.newContext({reducedMotion:'reduce'});
    await context.route('**/*',route=>route.request().url().startsWith(base+'/')?route.continue():route.abort());
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:900});
      await mechanisms(page,width);await about(page,width);await versions(page,width);await topicsAndNavigation(page,width);
      console.log(`PASS ${width}px: title-adjacent icons, states, keyboard, links and no overflow`);
    }
    assert.deepEqual(errors,[],'browser errors');
    console.log(`PASS screenshots: ${shots}`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
