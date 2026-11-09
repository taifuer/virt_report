/* Local-only source-list and historical-report presentation regression. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const shots = process.env.REPORT_SOURCES_SCREENSHOT_DIR || '/tmp/virt-report-report-sources';
fs.mkdirSync(shots, {recursive: true});

// Render isolated fixtures in memory: no runtime database or AI invocation.
const fixtures = JSON.parse(execFileSync(process.env.VIRT_REPORT_PYTHON || path.join(root, '.venv/bin/python'),
  ['-c', `
import json
from virt_report.config import Config
from virt_report.render.render import render_report_html
pages = {}
for period, key in (("daily", "2026-11-05"), ("weekly", "2026-W45"), ("monthly", "2026-11")):
    for only_protocol in (False, True):
        groups = [("virtio", "Virtio")] if only_protocol else [("qemu", "QEMU"), ("virtio", "Virtio")]
        content = {"period": period, "period_key": key, "label": "测试报告",
            "headline": "测试：队列提案评审", "model": "fixture-model", "fallback": False,
            "overview": [{"project": name, "summary": "本期未观察到显著动态。"}
                         for _, name in groups],
            "sections": [{"key": group, "name": name, "items": [{
                "title": name + " 队列提案", "summary": "保持历史正文与来源。",
                "url": "https://example.test/" + group, "source": "邮件列表",
                "time": "2026-11-05", "status": "评审中", "tag": "virtqueue"}]}
                         for group, name in groups],
            "stats": {"total_threads": len(groups), "total_items": len(groups),
                      "ml_patches": 0, "ml_rfc": 0, "gl_issues_opened": 0, "gl_mrs_merged": 0},
            "top_threads": [], "watchlist": []}
        pages[period + ("-protocol" if only_protocol else "-mixed")] = render_report_html(Config(), content)
print(json.dumps(pages))
`], {cwd: root, encoding: 'utf8', maxBuffer: 2 * 1024 * 1024}));

(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const context = await browser.newContext({reducedMotion: 'reduce'});
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    const fixture = url.pathname.match(/^\/__report-policy__\/([^/]+)\.html$/)?.[1];
    if (fixture && fixtures[fixture]) return route.fulfill({contentType: 'text/html', body: fixtures[fixture]});
    return route.continue();
  });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  try {
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(`${base}/about.html#data-sources`);
      const sources = page.locator('#data-sources');
      assert(await sources.isVisible());
      assert(await sources.locator('h2').evaluate(node =>
        node.getBoundingClientRect().top > document.querySelector('.site-head').getBoundingClientRect().bottom));
      assert.equal(await page.getByRole('heading', {name: '协议与后端补充来源'}).count(), 0);
      for (const name of ['virtio-dev', 'virtio-comment', 'vhost-user']) {
        const selector = `a[href="https://lore.kernel.org/${name}/"]`;
        assert.equal(await page.locator(selector).count(), 1);
        assert(await sources.locator(selector).isVisible());
      }
      assert.equal(await sources.getByText(/周报 \/ 月报/).count(), 2);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: path.join(shots, `sources-${width}.png`)});
      for (const name of Object.keys(fixtures)) {
        await page.goto(`${base}/__report-policy__/${name}.html`);
        assert(await page.locator('#virtio').isVisible(), `${name}: historical body`);
        assert(await page.locator('#virtio a[href="https://example.test/virtio"]').isVisible());
        const mixed = name.endsWith('-mixed');
        assert.deepEqual(await page.locator('#overview h3').allTextContents(), mixed ? ['QEMU'] : []);
        assert.equal(await page.locator('a[href="#overview"]').count(), mixed ? 1 : 0);
        assert.equal(await page.locator('.toc a[href="#virtio"]').count(), 1);
        assert(!await page.getByText('本期未观察到显著动态。', {exact: true}).count());
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name}/${width}: overflow`);
        if (name === 'weekly-mixed') await page.screenshot({path: path.join(shots, `weekly-${width}.png`), fullPage: true});
      }
    }
    assert.deepEqual(errors, []);
    console.log('PASS unified sources, historical daily/weekly/monthly bodies, overview and TOC, four viewport widths');
  } catch (error) {
    await page.screenshot({path: path.join(shots, 'failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
