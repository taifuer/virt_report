// Optional UI regression check. Requires Playwright and its Chromium browser.
// PLAYWRIGHT_MODULE may point to an existing installation outside this repository.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:8091/versions.html';

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1366, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const go = query => page.goto(`${base}${query}`);
    const visible = () => page.locator('[data-version-item]:visible');
    const members = topic => page.locator('[data-version-item]').evaluateAll(
      (items, topic) => items.filter(item => item.dataset.topics.split(',').includes(topic)).length, topic);
    const assertVisible = async (topic, project = '', year = '') => {
      const rows = await visible().evaluateAll(items => items.map(item => ({...item.dataset})));
      assert(rows.length > 0);
      assert(rows.every(row => row.topics.split(',').includes(topic)));
      if (project) assert(rows.every(row => row.project === project));
      if (year) assert(rows.every(row => row.year === year));
    };

    await go('?project=all&topic=migration&view=detailed');
    assert.equal(await visible().count(), 10);
    await assertVisible('migration');
    assert.equal(await page.locator('[data-version-count]').textContent(), `${await members('migration')} 个功能版本`);
    assert.equal(await page.locator('[data-version-topic] option[value="live-upgrade"]').count(), 0);
    await page.locator('[data-version-size]').selectOption('30');
    assert.equal(await visible().count(), 30);
    await page.locator('[data-version-next]').click();
    await page.reload();
    assert.equal(await visible().count(), Math.min(30, await members('migration') - 30));
    await assertVisible('migration');
    await page.locator('[data-version-project]').selectOption('kvm');
    await page.locator('[data-version-year]').selectOption('2021');
    await assertVisible('migration', 'kvm', '2021');
    assert.equal(await visible().count(), 2);
    await page.locator('[data-version-year]').selectOption('2020');
    assert.equal(await visible().count(), 0);
    assert(await page.locator('[data-version-empty]').isVisible());
    await go('?topic=migration&view=detailed#release-qemu-10.2.0');
    assert(await page.locator('[id="release-qemu-10.2.0"]').isVisible());
    assert.equal(await page.locator('[data-version-topic]').inputValue(), '');

    await go('?project=all&topic=vfio&view=detailed');
    assert.equal(await visible().count(), 10);
    await assertVisible('vfio');
    assert.equal(await page.locator('[data-version-count]').textContent(), `${await members('vfio')} 个功能版本`);
    await page.locator('[data-version-next]').click();
    assert.match(page.url(), /page=2/);
    await assertVisible('vfio');
    await page.locator('[data-version-topic]').selectOption('virtio');
    assert(!new URL(page.url()).searchParams.has('page'));
    await assertVisible('virtio');
    await page.locator('[data-version-size]').selectOption('20');
    assert.equal(await visible().count(), 20);
    await page.locator('[data-version-next]').click();
    await page.reload();
    assert.equal(await page.locator('[data-version-status]').textContent(), `2 / ${Math.ceil(await members('virtio') / 20)}`);
    await assertVisible('virtio');

    await page.locator('[data-version-project]').selectOption('qemu');
    await page.locator('[data-version-year]').selectOption('2020');
    await assertVisible('virtio', 'qemu', '2020');
    assert.equal(await visible().count(), 3);
    await page.locator('[data-version-project]').selectOption('kvm');
    assert.equal(await visible().count(), 0);
    assert(await page.locator('[data-version-empty]').isVisible());
    assert(await page.locator('[data-version-controls]').isHidden());

    await go('?project=qemu&topic=vfio&year=2012&view=detailed#release-kvm-5.15');
    assert(await page.locator('#release-kvm-5\\.15').isVisible());
    assert.equal(await page.locator('[data-version-topic]').inputValue(), '');
    assert.equal(await page.locator('[data-version-project]').inputValue(), '');
    assert.equal(await page.locator('[data-version-year]').inputValue(), '');

    await go('?project=invalid&topic=unknown&year=nope&page=-1&per_page=999');
    assert.equal(await page.locator('[data-version-topic]').inputValue(), '');
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'qemu');
    assert.equal(await page.locator('[data-version-size]').inputValue(), '10');

    await go('?project=all&topic=vfio');
    assert.equal(await visible().count(), await members('vfio'));
    const firstGroup = page.locator('[data-version-group]:visible').first();
    await firstGroup.locator('summary').click();
    assert.equal(await firstGroup.locator('details').getAttribute('open'), null);
    await page.locator('[data-version-topic]').selectOption('virtio');
    assert.equal(await visible().count(), await members('virtio'));
    await page.locator('[data-version-view="detailed"]').click();
    assert.equal(await visible().count(), 10);

    // Newest-release links also escape conflicting theme filters.
    const latestKVM = page.locator('[data-version-jump][href^="#release-kvm-"]');
    const latestKVMId = (await latestKVM.getAttribute('href')).slice(1);
    await latestKVM.click();
    assert(await page.locator(`[id="${latestKVMId}"]`).isVisible());

    for (const width of [320, 375, 390, 768, 1366]) {
      await page.setViewportSize({width, height: 900});
      for (const topic of ['migration', 'vfio']) {
        await go(`?project=all&topic=${topic}&view=detailed`);
        const dimensions = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth]);
        assert.equal(dimensions[0], dimensions[1], `horizontal overflow in ${topic} at ${width}px`);
        await assertVisible(topic);
      }
    }
    assert.deepEqual(errors, []);
    console.log('Version filters, pagination, deep links, empty states and five viewport widths passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
