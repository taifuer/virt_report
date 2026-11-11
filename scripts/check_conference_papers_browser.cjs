/* Serial, local-only checks for the classic filter and compact paper rows. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:8091').replace(/\/$/, '');
const url = `${base}/conference-papers.html`;
const shots = process.env.PAPER_SCREENSHOT_DIR || '/tmp/virt-paper-browser';
const raw = JSON.parse(fs.readFileSync(path.join(__dirname, '../virt_report/content/conferences.json')));
const readings = JSON.parse(fs.readFileSync(path.join(__dirname, '../virt_report/content/paper_readings.json')));
const selected = raw.papers.filter(paper => Object.hasOwn(readings.papers, paper.id));
const selectedCount = selected.length;
const reviews = raw.papers.filter(paper => ['survey', 'monograph'].includes(paper.publication_type));
const years = papers => [...new Set(papers.map(paper => paper.year))].sort((a, b) => b - a).map(String);
const visible = '[data-conference-item]:visible';
const classic = page => page.locator('[data-conference-filter="curated"]');
const surveyFilter = page => page.locator('[data-conference-filter="surveys"]');
const options = async (page, key) => page.locator(`[data-conference-${key}] option`).evaluateAll(nodes =>
  nodes.filter(n => n.value).map(n => n.value));
async function ready(page) {
  await page.locator('[data-conference-enhanced]:visible').waitFor();
}
async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no horizontal overflow');
}
async function facetCounts(page, papers, key) {
  const actual = await page.locator(`[data-conference-${key}] option`).evaluateAll(nodes =>
    nodes.map(node => ({value: node.value, label: node.textContent})));
  for (const option of actual) {
    const count = option.value ? papers.filter(p => String(p[key]) === option.value).length : papers.length;
    assert(option.label.endsWith(`（${count}）`), `${key}/${option.value}: correct filtered count`);
    assert(!option.value || count > 0, 'unavailable options remain absent');
  }
}
async function controlGeometry(page) {
  return page.evaluate(() => Object.fromEntries([
    '.conference-controls', '[data-conference-venue]', '[data-conference-year]', '[data-conference-sort]',
    '.paper-quick-filters', '[data-conference-filter="curated"]', '[data-conference-filter="surveys"]', '.paper-reset-slot',
  ].map(selector => {
    const box = document.querySelector(selector).getBoundingClientRect();
    return [selector, {x: box.x + scrollX, y: box.y + scrollY, width: box.width, height: box.height}];
  })));
}
async function stableControls(page, baseline, state) {
  const actual = await controlGeometry(page);
  for (const selector of Object.keys(baseline)) {
    for (const key of ['x', 'y', 'width', 'height']) {
      assert(Math.abs(actual[selector][key] - baseline[selector][key]) <= 1,
        `${state}: ${selector} ${key} stays stable (${baseline[selector][key]} / ${actual[selector][key]})`);
    }
  }
  assert.equal(actual['[data-conference-venue]'].width, actual['[data-conference-year]'].width);
  assert.equal(actual['[data-conference-venue]'].height, actual['[data-conference-year]'].height);
}
(async () => {
  fs.mkdirSync(shots, {recursive: true});
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext();
    await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort());
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    for (const width of [1440, 1100, 1051, 1050, 861, 768, 640, 620, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      await page.goto(url); await ready(page);
      const geometry = await controlGeometry(page);
      assert.equal(await page.locator('[data-conference-item][data-curated="true"] h2 .paper-classic-mark').count(), selectedCount);
      assert.equal(await page.locator('[data-conference-item][data-curated="false"] .paper-classic-mark').count(), 0);
      assert.equal(await classic(page).count(), 1);
      assert.equal(await page.getByRole('button', {name: '经典', exact: true}).count(), 1);
      assert.equal(await page.getByRole('button', {name: '综述', exact: true}).count(), 1);
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'false', 'default is all papers');
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false');
      assert.deepEqual(await options(page, 'year'), years(raw.papers));
      await facetCounts(page, raw.papers, 'year');
      await facetCounts(page, raw.papers, 'venue');
      assert.equal(await page.locator(visible).first().getAttribute('data-year'), years(raw.papers)[0]);
      assert.equal(await page.locator('.paper-switch-track, [role="switch"]').count(), 0);
      const gap = await page.evaluate(() => document.querySelector('.paper-search').getBoundingClientRect().top -
        document.querySelector('.conference-page > header').getBoundingClientRect().bottom);
      assert(gap >= 12 && gap <= 18, `compact intro/search spacing: ${gap}`);
      assert.equal(await page.locator('.research-nav').count(), 0, 'no duplicate paper/conference/forum tabs');
      assert.equal(await page.locator('.conference-page > header .kicker').count(), 0);
      assert.equal(await page.locator('.conference-page > header a').count(), 0, 'remove the redundant conference link');
      await page.screenshot({path: path.join(shots, `all-papers-${width}.png`)});
      await surveyFilter(page).click();
      assert.equal(new URL(page.url()).searchParams.get('scope'), 'surveys');
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true');
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator(visible).count(), 5, 'four surveys and one book');
      assert(await page.locator('#paper-slca20-memory-consistency').isVisible());
      assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n =>
        ['survey', 'monograph'].includes(n.dataset.publicationType))));
      await facetCounts(page, reviews, 'venue');
      await facetCounts(page, reviews, 'year');
      assert.deepEqual(await options(page, 'year'), years(reviews));
      await stableControls(page, geometry, `surveys/${width}`);
      assert.notEqual(await surveyFilter(page).evaluate(node => getComputedStyle(node).backgroundColor),
        await classic(page).evaluate(node => getComputedStyle(node).backgroundColor), 'selected filter is highlighted');
      await noOverflow(page);
      await page.screenshot({path: path.join(shots, `review-filter-${width}.png`)});
      await page.reload(); await ready(page);
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true', 'review filter survives reload');
      await page.locator('[data-conference-venue]').selectOption('slca');
      assert.equal(await page.locator(visible).count(), 1);
      assert.deepEqual(await options(page, 'year'), ['2020']);
      await page.locator('[data-conference-reset]').click();
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator(visible).count(), 5);
      await classic(page).click();
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'true');
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false', 'quick filters are mutually exclusive');
      await stableControls(page, geometry, `review-to-classic/${width}`);
      await page.goBack(); await ready(page);
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true');
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator(visible).count(), 5, 'back restores survey scope');
      await surveyFilter(page).click();
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false', 'click active filter again to clear it');
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
      assert.equal(new URL(page.url()).searchParams.get('scope'), null);
      await stableControls(page, geometry, `review-to-all/${width}`);
      await classic(page).click();
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'true');
      assert.equal(new URL(page.url()).searchParams.get('scope'), 'curated');
      await page.reload(); await ready(page);
      await stableControls(page, geometry, `classic/${width}`);
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'true', 'explicit filter survives reload');
      assert.equal(await page.locator(visible).count(), Math.min(10, selectedCount));
      assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n => n.dataset.curated === 'true')));
      assert.equal(await page.locator(visible).first().getAttribute('data-year'), years(selected)[0]);
      assert.deepEqual(await options(page, 'year'), years(selected));
      assert.deepEqual((await options(page, 'venue')).sort(), [...new Set(selected.map(p => p.venue))].sort());
      assert.equal(await page.locator('.paper-policy, .paper-resources, [data-conference-scope], [data-conference-count]').count(), 0);
      assert.equal(await page.locator('.paper-reading').count(), 0, 'reading guides are removed');
      assert.equal(await page.getByText('阅读指引', {exact: true}).count(), 0);
      assert(await page.locator(visible).first().locator('h2 .paper-resource-tag').filter({hasText: /^PDF$/}).isVisible());
      await facetCounts(page, selected, 'year');
      await facetCounts(page, selected, 'venue');
      const filtersBox = await page.locator('.paper-quick-filters').boundingBox();
      const classicBox = await classic(page).boundingBox(), reviewBox = await surveyFilter(page).boundingBox();
      const barBox = await page.locator('.conference-controls').boundingBox();
      assert(barBox.x + barBox.width - filtersBox.x - filtersBox.width <= 21, 'quick filters align on the right');
      assert.equal(classicBox.width, reviewBox.width, 'buttons have the same width');
      assert.equal(classicBox.height, reviewBox.height, 'buttons have the same height');
      assert.notEqual(await classic(page).evaluate(node => getComputedStyle(node).backgroundColor),
        await surveyFilter(page).evaluate(node => getComputedStyle(node).backgroundColor), 'classic filter is highlighted');
      await noOverflow(page);
      if (width <= 620) assert(await page.locator(visible).first().locator('.paper-meta')
        .evaluate(node => parseFloat(getComputedStyle(node).columnGap) >= 8), 'mobile metadata has breathing room');
      if (width <= 620) {
        const venueBox = await page.locator('[data-conference-venue]').boundingBox();
        const yearBox = await page.locator('[data-conference-year]').boundingBox();
        assert.equal(venueBox.x, yearBox.x, 'mobile select fields align');
        assert.equal(venueBox.width, yearBox.width);
      }
      await page.screenshot({path: path.join(shots, `classics-${width}.png`)});
      await page.locator('[data-conference-query]').fill('Ingens');
      assert.equal(await page.locator(visible).count(), 1);
      const ingens = page.locator('#paper-osdi16-ingens');
      assert(await ingens.isVisible());
      assert.equal(await ingens.locator('.editor-comment').count(), 1);
      assert.equal(await ingens.locator(':scope > .paper-body > p').count(), 2, 'content and commentary stay merged');
      assert(await ingens.locator('h2 .paper-classic-mark svg').isVisible());
      await stableControls(page, geometry, `query/${width}`);
      const comment = await ingens.locator('.editor-comment').innerText();
      for (const term of ['4.3', 'Cloudstone', '18%', '两个网页', '0.7%', '后台扫描']) {
        assert(comment.includes(term), `Ingens retains evaluation context: ${term}`);
      }
      await noOverflow(page);
      await page.screenshot({path: path.join(shots, `ingens-${width}.png`)});
      await page.locator('[data-conference-query]').fill('');
      await page.locator('[data-conference-year]').selectOption(years(selected)[0]);
      const latest = selected.filter(p => String(p.year) === years(selected)[0]);
      assert.equal(await page.locator(visible).count(), latest.length);
      assert.deepEqual((await options(page, 'venue')).sort(), [...new Set(latest.map(p => p.venue))].sort());
      await facetCounts(page, latest, 'venue');
      await page.locator('[data-conference-reset]').click();
      await stableControls(page, geometry, `reset/${width}`);
      await page.locator('[data-conference-next]').click();
      assert.equal(await page.locator(visible).count(), Math.min(10, selectedCount - 10));
      if (selectedCount > 20) await page.locator('[data-conference-next]').click();
      assert.equal(await page.locator(visible).count(), selectedCount % 10 || 10);
      assert(await page.locator('[data-conference-next]').isDisabled());
      await classic(page).click();
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
      assert.equal(new URL(page.url()).searchParams.get('scope'), null, 'default scope has a clean URL');
      assert.deepEqual(await options(page, 'year'), years(raw.papers));
      assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false');
      await facetCounts(page, raw.papers, 'year');
      const orders = await page.locator(visible).evaluateAll(nodes => nodes.map(n => Number(n.dataset.order)));
      assert.deepEqual(orders, Array.from({length: 10}, (_, i) => i));
      await page.locator('[data-conference-year]').selectOption('2026');
      assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n => n.dataset.year === '2026')));
      await classic(page).click();
      assert.equal(await page.locator('[data-conference-year]').inputValue(), '', 'unavailable year clears on scope change');
      assert.deepEqual(await options(page, 'year'), years(selected));
      assert.equal(new URL(page.url()).searchParams.get('year'), null);
      await page.goBack(); await ready(page);
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator('[data-conference-year]').inputValue(), '2026', 'history restores options removed in classic scope');
      await page.locator('[data-conference-reset]').click();
      await page.locator('[data-conference-venue]').selectOption('asplos');
      await facetCounts(page, raw.papers.filter(p => p.venue === 'asplos'), 'year');
      await page.locator('[data-conference-year]').selectOption('2020');
      await page.locator('[data-conference-query]').fill('REAP');
      assert.equal(await page.locator(visible).count(), 1);
      assert.equal(await page.locator(visible).getAttribute('id'), 'paper-asplos21-reap');
      assert.deepEqual(await options(page, 'year'), ['2021']);
      assert.deepEqual(await options(page, 'venue'), ['asplos']);
      await facetCounts(page, raw.papers.filter(p => p.id === 'asplos21-reap'), 'year');
      assert.equal(await page.locator('[data-conference-year]').inputValue(), '');
      await classic(page).click();
      assert.equal(await page.locator(visible).count(), 0);
      assert(await page.locator('[data-conference-empty]').isVisible());
      assert.deepEqual(await options(page, 'year'), []);
      assert.deepEqual(await options(page, 'venue'), []);
      await stableControls(page, geometry, `empty/${width}`);
      await classic(page).click();
      assert.equal(await page.locator(visible).count(), 1);
      await page.goBack(); await ready(page);
      assert.equal(await classic(page).getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator(visible).count(), 0);
      await page.locator('[data-conference-reset]').click();
      assert.equal(await page.locator(visible).count(), 10);
      await page.locator('[data-conference-sort]').selectOption('oldest');
      await stableControls(page, geometry, `sort/${width}`);
      assert.equal(await page.locator(visible).first().getAttribute('data-year'), years(raw.papers).at(-1));
      assert.equal(new URL(page.url()).searchParams.get('sort'), 'oldest');
      await page.reload(); await ready(page);
      assert.equal(await page.locator('[data-conference-sort]').inputValue(), 'oldest');
      await page.locator('[data-conference-next]').click();
      assert.equal(new URL(page.url()).searchParams.get('page'), '2');
      await page.locator('[data-conference-sort]').selectOption('newest');
      assert.equal(new URL(page.url()).searchParams.get('page'), null, 'sorting starts at first page');
      assert.equal(new URL(page.url()).searchParams.get('sort'), null);
      await page.goBack(); await ready(page);
      assert.equal(await page.locator('[data-conference-sort]').inputValue(), 'oldest');
      assert.equal(new URL(page.url()).searchParams.get('page'), '2', 'history restores sorting and page');
      await noOverflow(page);
    }
    await page.goto(`${url}#paper-asplos21-reap`); await ready(page);
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
    assert(await page.locator('#paper-asplos21-reap').isVisible(), 'ordinary search/deep links remain reachable');
    await page.reload(); await ready(page);
    assert(await page.locator('#paper-asplos21-reap').isVisible());
    await page.goto(`${url}?sort=oldest#paper-asplos21-reap`); await ready(page);
    assert(await page.locator('#paper-asplos21-reap').isVisible(), 'deep links use the selected order');
    assert.equal(await page.locator('[data-conference-sort]').inputValue(), 'oldest');
    await page.goto(`${url}?sort=invalid`); await ready(page);
    assert.equal(await page.locator('[data-conference-sort]').inputValue(), 'newest');
    await page.goto(`${url}?year=2026`); await ready(page);
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'false', 'legacy filters use all papers');
    assert(await page.locator(visible).evaluateAll(nodes => nodes.every(n => n.dataset.year === '2026')));
    await page.goto(`${url}?scope=curated&year=2026`); await ready(page);
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('[data-conference-year]').inputValue(), '');
    assert.equal(await page.locator(visible).count(), 10, 'unavailable facet clears without widening scope');
    await page.goto(`${url}?scope=all&q=REAP`); await ready(page);
    assert.equal(await page.locator(visible).count(), 1);
    await page.locator(visible).locator('[data-paper-topic="Serverless"]').click();
    assert(new URL(page.url()).searchParams.get('topic') === 'Serverless');
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
    await classic(page).focus(); await page.keyboard.press('Enter');
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'true', 'filter is keyboard operable');
    await page.keyboard.press('Space');
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'false', 'Space toggles the filter');
    await page.goto(`${url}?scope=surveys&year=1974`); await ready(page);
    assert.equal(await page.locator('[data-conference-year]').inputValue(), '');
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator(visible).count(), 5, 'unavailable year clears within survey scope');
    await page.goto(`${url}?scope=surveys&sort=oldest#paper-slca20-memory-consistency`); await ready(page);
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true', 'deep link retains a matching scope');
    assert.equal(await page.locator(visible).first().getAttribute('id'), 'paper-slca20-memory-consistency');
    await page.goto(`${url}?scope=surveys#paper-osdi16-ingens`); await ready(page);
    assert(await page.locator('#paper-osdi16-ingens').isVisible(), 'cross-scope deep link remains reachable');
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false');
    await page.goto(`${url}?scope=unknown`); await ready(page);
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false');
    assert.equal(await classic(page).getAttribute('aria-pressed'), 'false');
    await surveyFilter(page).focus(); await page.keyboard.press('Enter');
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true');
    await page.locator('[data-conference-query]').fill('unmatched survey query');
    assert.equal(await page.locator(visible).count(), 0);
    assert(await page.locator('[data-conference-empty]').isVisible());
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'true', 'empty search retains the selected scope');
    await page.locator('[data-conference-reset]').click();
    assert.equal(await page.locator(visible).count(), 5);
    await surveyFilter(page).focus(); await page.keyboard.press('Space');
    assert.equal(await surveyFilter(page).getAttribute('aria-pressed'), 'false');
    for (const [paperId, query, venue, year, source] of [
      ['systor13-blk-mq', 'blk-mq', 'systor', '2013', 'https://doi.org/10.1145/2485732.2485740'],
      ['atc12-netmap', 'netmap', 'atc', '2012', 'https://www.usenix.org/conference/atc12/technical-sessions/presentation/rizzo'],
      ['eurosys16-linux-scheduler', 'Wasted Cores', 'eurosys', '2016', 'https://doi.org/10.1145/2901318.2901326'],
      ['asplos20-mitosis', 'Mitosis', 'asplos', '2020', 'https://doi.org/10.1145/3373376.3378468'],
      ['nsdi19-shinjuku', 'Shinjuku', 'nsdi', '2019', 'https://www.usenix.org/conference/nsdi19/presentation/kaffes'],
    ]) {
      await page.goto(`${url}?scope=curated&venue=${venue}&year=${year}&q=${query}`); await ready(page);
      const row = page.locator(`#paper-${paperId}`);
      assert(await row.isVisible(), `${paperId}: available through combined filters`);
      assert.equal(await row.locator('h2 a').first().getAttribute('href'), source);
      assert.equal(await row.locator('.paper-classic-mark').count(), 1);
      await page.goto(`${url}#paper-${paperId}`); await ready(page);
      assert(await row.isVisible(), `${paperId}: stable deep link`);
      await row.screenshot({path: path.join(shots, `${paperId}.png`)});
    }
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      const surveys = raw.papers.filter(paper => paper.publication_type === 'survey');
      await page.goto(`${url}?topic=${encodeURIComponent('综述')}`); await ready(page);
      assert.equal(await page.locator(visible).count(), 4, 'survey topic finds all four new reviews');
      await facetCounts(page, surveys, 'venue');
      await facetCounts(page, surveys, 'year');
      await noOverflow(page);
      await page.screenshot({path: path.join(shots, `surveys-${width}.png`)});
      for (const paper of raw.papers.filter(paper => ['survey', 'monograph'].includes(paper.publication_type))) {
        await page.goto(`${url}?scope=all&venue=${paper.venue}&year=${paper.year}&q=${encodeURIComponent(paper.title)}`);
        await ready(page);
        const row = page.locator(`#paper-${paper.id}`);
        assert(await row.isVisible(), `${paper.id}: source/year/search find the entry`);
        assert.equal(await page.locator(visible).count(), 1);
        assert.equal(await row.locator('h2 a').first().getAttribute('href'), paper.url);
        assert.equal(await row.locator('.paper-resource-tag').first().getAttribute('href'), paper.pdf_url);
        assert.equal(await row.locator('.paper-classic-mark').count(), 0, 'a survey is not automatically starred');
        assert.equal(await row.locator('.paper-body > p').count(), 2, 'content and commentary stay together');
        await noOverflow(page);
        await page.goto(`${url}#paper-${paper.id}`); await ready(page);
        assert(await row.isVisible(), `${paper.id}: searchable deep link works`);
        await noOverflow(page);
        if (paper.publication_type === 'monograph') {
          await row.screenshot({path: path.join(shots, `monograph-${width}.png`)});
        }
      }
      await page.goto(`${url}?venue=slca`); await ready(page);
      assert.equal(await page.locator(visible).count(), 1, 'book series is a selectable source');
      assert.deepEqual(await options(page, 'year'), ['2020']);
      assert((await page.locator('.conference-page > header').innerText()).includes('126 篇论文、1 部专著'));
      await page.goto(url); await ready(page);
      for (const paperId of ['cacm68-working-set', 'osdi94-lottery']) {
        assert.equal(await page.locator(`#paper-${paperId}`).count(), 0, 'removed papers stay out of the catalogue');
      }
      const mainConference = page.locator('.main-nav').getByRole('link', {name: '会议', exact: true, includeHidden: true});
      assert.equal(await mainConference.getAttribute('href'), 'conferences.html');
      assert.equal(await mainConference.getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('.main-nav').getByRole('link', {name: '论文', exact: true, includeHidden: true}).count(), 0);
      assert.equal(await page.getByRole('link', {name: '会议目录', exact: true}).count(), 0);
      await page.goto(`${base}/conferences.html`);
      assert.equal(new URL(page.url()).pathname, '/conferences.html');
      assert.equal(await page.locator('.conference-page > header .kicker, .conference-page > header nav').count(), 0);
      assert.equal(await page.getByRole('link', {name: '查看相关论文', exact: true}).getAttribute('href'), 'conference-papers.html');
      await page.screenshot({path: path.join(shots, `conference-directory-${width}.png`)});
      await page.locator('a[href="kvm-forum.html"]').click();
      const crumbs = page.getByRole('navigation', {name: '面包屑'});
      assert.deepEqual(await crumbs.locator('a').evaluateAll(nodes => nodes.map(node => node.getAttribute('href'))),
        ['conferences.html']);
      await noOverflow(page);
      await crumbs.getByRole('link', {name: '会议', exact: true}).click();
      assert.equal(new URL(page.url()).pathname, '/conferences.html');
      await page.getByRole('link', {name: '查看相关论文', exact: true}).click(); await ready(page);
      await page.goto(`${base}/versions.html`);
      assert.equal(await page.locator('.versions-header .kicker, .version-latest-label').count(), 0);
      assert.equal(await page.locator('.version-latest').getAttribute('aria-label'), '最新软件版本与协议规范');
      assert.equal(await page.locator('.version-latest a').count(), 6, 'all latest projects remain reachable');
      await noOverflow(page);
      await page.screenshot({path: path.join(shots, `versions-heading-${width}.png`)});
    }
    assert.deepEqual(errors, []);
    await context.close();
    const plain = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 900}});
    await plain.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort());
    const plainPage = await plain.newPage();
    await plainPage.goto(url);
    assert.equal(await plainPage.locator(visible).count(), raw.papers.length, 'all papers readable without scripts');
    assert.equal(await plainPage.locator(visible).first().getAttribute('data-year'), years(raw.papers)[0]);
    await noOverflow(plainPage);
    await plainPage.screenshot({path: path.join(shots, 'papers-no-js.png')});
    await plain.close();
    console.log(`PASS paper filters: ${selectedCount} classics / ${reviews.length} surveys and books / ${raw.papers.length} total; mutually exclusive toggles, stable controls, facets, sort, history, deep links, responsive, no-JS`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
