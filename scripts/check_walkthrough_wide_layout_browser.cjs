/* Full-width walkthrough diagrams and above-diagram explanations, including embeds. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const shots = process.env.WALKTHROUGH_WIDE_SCREENSHOT_DIR;
if (shots) fs.mkdirSync(shots, {recursive: true});
const cases = [
  {slug: 'virtio-blk-read', prefix: 'vbr', detail: 'mechanism'},
  {slug: 'network-io', prefix: 'execution', detail: 'structure'},
  {slug: 'cpu-execution', prefix: 'execution', detail: 'structure'},
  {slug: 'memory-access', prefix: 'execution', detail: 'structure'},
  {slug: 'interrupt-delivery', prefix: 'execution', detail: 'structure'},
  {slug: 'gpu-execution', prefix: 'execution', detail: 'structure'},
  {slug: 'gpu-execution', query: '?scenario=vfio-readback', prefix: 'execution', detail: 'structure'},
  {slug: 'gpu-execution', query: '?scenario=mdev-copy', prefix: 'execution', detail: 'structure'},
  {slug: 'virtio-blk-read', query: '?backend=rbd-blk', prefix: 'vbr', detail: 'mechanism'},
  {slug: 'virtio-blk-read', query: '?backend=spdk-blk', prefix: 'vbr', detail: 'mechanism'},
  {slug: 'libvirt-domain-start', prefix: 'execution', detail: 'structure'},
];

async function checkLayout(lab, tag) {
  const result = await lab.evaluate(root => {
    const workspace = root.querySelector('.walkthrough-workspace');
    const inspector = workspace.querySelector('.walkthrough-inspector');
    const visual = workspace.querySelector('.vbr-visual, .execution-canvas');
    const bounds = node => {
      const r = node.getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height};
    };
    const paragraphs = [...inspector.querySelectorAll('.walkthrough-step-explanation')]
      .filter(node => node.getClientRects().length && node.textContent.trim());
    return {
      workspace: bounds(workspace), inspector: bounds(inspector), visual: bounds(visual),
      explanationFirst: workspace.firstElementChild === inspector,
      separator: getComputedStyle(inspector).borderLeftWidth,
      pageWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth,
      paragraphs: paragraphs.map(node => ({...bounds(node), font: getComputedStyle(node).fontSize,
        overflow: node.scrollHeight > node.clientHeight + 1, hidden: getComputedStyle(node).overflowY})),
      inspectorClipped: inspector.scrollHeight > inspector.clientHeight + 1,
      sourcesInInspector: inspector.querySelectorAll('a').length,
    };
  });
  assert(result.explanationFirst, `${tag}: explanation must come first in DOM reading order`);
  assert(result.inspector.bottom <= result.visual.top + 1, `${tag}: explanation must be above the diagram`);
  assert(Math.abs(result.visual.width - result.workspace.width) <= 2,
    `${tag}: diagram must use the full workspace ${JSON.stringify(result)}`);
  assert.equal(result.separator, '0px', `${tag}: old sidebar divider must not remain`);
  assert(!result.inspectorClipped, `${tag}: explanations must not be clipped`);
  assert.equal(result.sourcesInInspector, 0, `${tag}: keep source links in the source view`);
  assert.equal(result.paragraphs.length, 1, `${tag}: one continuous step explanation`);
  for (const paragraph of result.paragraphs) {
    assert.equal(paragraph.font, '16px', `${tag}: explanation keeps readable body size`);
    assert(!paragraph.overflow && !['hidden', 'clip', 'scroll'].includes(paragraph.hidden),
      `${tag}: body text must wrap rather than be clipped ${JSON.stringify(paragraph)}`);
    assert(paragraph.left >= result.inspector.left - 1 && paragraph.right <= result.inspector.right + 1,
      `${tag}: explanation must stay within the workspace`);
  }
  assert(result.pageWidth <= result.viewportWidth + 1, `${tag}: page overflow ${JSON.stringify(result)}`);
  return result;
}

async function checkComponents(lab, tag) {
  const result = await lab.evaluate(root => {
    const visible = node => node.getClientRects().length > 0;
    const components = [...root.querySelectorAll('[data-component]')].filter(visible).map(node => node.dataset.component);
    const guestCards = [...root.querySelectorAll('[data-component="guest"] [data-execution-node]')].filter(visible);
    const badges = [...root.querySelectorAll('.execution-node-heading:has(.walkthrough-node-badge), .vbr-node-heading')]
      .filter(visible).map(node => {
        const title = node.querySelector('strong, b'), badge = node.querySelector('.walkthrough-node-badge, .vbr-mode-tag');
        const rect = item => {
          const r = item.getBoundingClientRect();
          return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
        };
        return {heading: rect(node), title: rect(title), badge: rect(badge), text: badge.textContent};
      });
    return {components, badges, guestCards: guestCards.length};
  });
  for (const component of ['guest', 'kvm', 'qemu', 'host-kernel', 'hardware']) {
    assert(result.components.includes(component), `${tag}: visible basic component ${component} is missing`);
  }
  for (const {heading, title, badge, text} of result.badges) {
    assert(badge.left >= heading.left - 1 && badge.right <= heading.right + 1,
      `${tag}: ${text} badge escapes its node`);
    assert(title.right <= badge.left + 1 || title.bottom <= badge.top + 1,
      `${tag}: badge overlaps node label ${JSON.stringify({title, badge})}`);
  }
  if (tag.includes('gpu-execution')) assert(result.badges.length >= 2,
    `${tag}: GPU guest user/kernel mode badges must be present`);
}

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return ['localhost', '127.0.0.1'].includes(host) ? route.continue() : route.abort();
  });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const width of [1920, 1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: width > 700 ? 1080 : 844});
      let largestExplanation = 0;
      for (const item of cases) {
        const label = `${item.slug}${item.query || ''}`;
        await page.goto(`${base}/mechanisms/${item.slug}.html${item.query || ''}`);
        const lab = page.locator('[data-walkthrough-lab]').first();
        await lab.locator(`[data-${item.prefix}-next]`).waitFor({state: 'visible'});
        for (const position of ['initial', 'middle', 'last']) {
          if (position !== 'initial') {
            await lab.evaluate((root, {prefix, position}) => {
              const next = root.querySelector(`[data-${prefix}-next]`);
              const limit = position === 'middle' ? 4 : 150;
              let count = 0;
              while (!next.disabled && count++ < limit) next.click();
              if (position === 'last' && !next.disabled) throw new Error('Playback did not finish');
            }, {prefix: item.prefix, position});
          }
          for (const view of ['overview', item.detail, 'source']) {
            await lab.locator(`[data-${item.prefix}-view="${view}"]`).click();
            const tag = `${label}/${width}/${position}/${view}`;
            const result = await checkLayout(lab, tag);
            if (view === 'overview') await checkComponents(lab, tag);
            largestExplanation = Math.max(largestExplanation, result.inspector.height);
          }
        }
        await lab.locator(`[data-${item.prefix}-view="overview"]`).click();
        const step = await lab.getAttribute('data-step');
        const expand = lab.locator('[data-walkthrough-expand]');
        await expand.click();
        await page.waitForFunction(() => document.querySelector('[data-walkthrough-expand]').getAttribute('aria-pressed') === 'true');
        await checkLayout(lab, `${label}/${width}/fullscreen`);
        await checkComponents(lab, `${label}/${width}/fullscreen`);
        assert.equal(await lab.getAttribute('data-step'), step, `${item.slug}: fullscreen preserves playback state`);
        if (shots && [1440, 390].includes(width)) {
          await lab.evaluate(root => {
            const workspace = root.querySelector('.walkthrough-workspace');
            root.scrollTop += workspace.getBoundingClientRect().top - root.getBoundingClientRect().top - 16;
          });
          const name = label.replace(/[^a-z0-9-]/gi, '-');
          await page.screenshot({path: path.join(shots, `${name}-${width}-wide.png`)});
        }
        await expand.click();
      }
      console.log(`PASS above-diagram explanations and full-width views: ${width}px (longest explanation ${Math.round(largestExplanation)}px, no clipping)`);
    }
    assert.deepEqual(errors, []);
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
