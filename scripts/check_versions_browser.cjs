// Optional UI regression check. Requires Playwright and its Chromium browser.
// PLAYWRIGHT_MODULE may point to an existing installation outside this repository.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchBrowser} = require('./browser_runtime.cjs');
const base = process.argv[2] || 'http://127.0.0.1:8091/versions.html';
const shots = process.env.VERSIONS_SCREENSHOT_DIR || '/tmp';
fs.mkdirSync(shots, {recursive: true});

(async () => {
  const browser = await launchBrowser({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1366, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const go = query => page.goto(`${base}${query}`);
    const visible = () => page.locator('[data-version-item]:visible');
    const members = topic => page.locator('[data-version-item]').evaluateAll(
      (items, topic) => items.filter(item => item.dataset.topics.split(',').includes(topic)).length, topic);
    const yearOptions = () => page.locator('[data-version-year] option').evaluateAll(
      options => options.map(option => option.value));
    const assertAvailableYears = async () => {
      const project = await page.locator('[data-version-project]').inputValue();
      const topic = await page.locator('[data-version-topic]').inputValue();
      const years = await page.locator('[data-version-item]').evaluateAll((items, {project, topic}) =>
        [...new Set(items.filter(item => (!project || item.dataset.project === project)
          && (!topic || item.dataset.topics.split(',').includes(topic))).map(item => item.dataset.year))]
          .sort().reverse(), {project, topic});
      assert.deepEqual(await yearOptions(), ['', ...years]);
    };
    const assertVisible = async (topic, project = '', year = '') => {
      const rows = await visible().evaluateAll(items => items.map(item => ({...item.dataset})));
      assert(rows.length > 0);
      assert(rows.every(row => row.topics.split(',').includes(topic)));
      if (project) assert(rows.every(row => row.project === project));
      if (year) assert(rows.every(row => row.year === year));
    };

    await go('?project=all&topic=migration&view=detailed');
    await assertAvailableYears();
    assert.equal(await page.locator('label[for="version-topic"]').evaluate(label => label.firstChild.textContent.trim()), '主题');
    assert.deepEqual(await page.locator('[data-version-topic] option').allTextContents(),
      ['全部主题', '设备直通 / VFIO', '虚拟 I/O / virtio', '热迁移']);
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
    await assertAvailableYears();
    await page.locator('[data-version-year]').selectOption('2021');
    await assertVisible('migration', 'kvm', '2021');
    assert.equal(await visible().count(), 2);
    assert(!(await yearOptions()).includes('2020')); // No KVM migration entries in 2020.
    await go('?project=kvm&topic=migration&year=2020&view=detailed');
    assert.equal(await page.locator('[data-version-year]').inputValue(), '');
    assert(!new URL(page.url()).searchParams.has('year'));
    await assertAvailableYears();
    await assertVisible('migration', 'kvm');
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
    assert.equal(await page.locator('[data-version-year]').inputValue(), '');
    assert(!new URL(page.url()).searchParams.has('year'));
    await assertAvailableYears();
    assert.equal(await visible().count(), 0);
    assert.deepEqual(await yearOptions(), ['']);
    assert(await page.locator('[data-version-empty]').isVisible());

    // Unsupported theme parameters must not filter backend software releases.
    await go('?project=spdk&topic=migration&view=detailed');
    assert(await visible().count() > 0);
    assert.equal(await page.locator('[data-version-topic]').inputValue(), '');
    assert(!new URL(page.url()).searchParams.has('topic'));
    assert(await page.locator('label[for="version-topic"]').isHidden());
    assert(await page.locator('[data-version-empty]').isHidden());
    await assertAvailableYears();

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

    // Protocol specifications share the browser while retaining document stages.
    await go('?project=virtio');
    await assertAvailableYears();
    assert.deepEqual(await yearOptions(), ['', '2026', '2023', '2022', '2019', '2016', '2012', '2010']);
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'virtio');
    const specCount = await page.locator('[data-version-item][data-kind="specification"]').count();
    assert(specCount >= 6);
    assert.equal(await visible().count(), specCount);
    assert.equal(await page.locator('[data-version-count]').textContent(), `${specCount} 份规范文档`);
    assert(await page.locator('[data-version-spec-note]').isVisible());
    assert(await page.locator('[data-version-software-sources]').isHidden());
    assert(await page.locator('label[for="version-topic"]').isHidden());
    assert.equal(await visible().locator('.version-notes:visible').count(), 0);
    assert.equal(await visible().locator('.version-actions a[href$=".pdf"]:visible').count(), specCount);
    assert.match(await page.locator('[id="release-virtio-1.3-csd01"]').textContent(), /委员会规范草案/);
    const legacy = page.locator('[id="release-virtio-0.9.5-legacy"]');
    assert.match(await legacy.textContent(), /2012-05-07[\s\S]*Legacy[\s\S]*早期规范草案/);
    assert.equal(await legacy.locator('.version-actions a[href$=".pdf"]').getAttribute('href'),
      'https://ozlabs.org/~rusty/virtio-spec/virtio-0.9.5.pdf');
    assert.match(await page.locator('[data-version-jump][href^="#release-virtio-"]').textContent(), /1\.4/);
    await page.locator('[data-version-spec-note] summary').click();
    assert.match(await page.locator('[data-version-spec-note] details').textContent(), /VIRTIO_F_VERSION_1/);
    assert.match(await page.locator('[data-version-spec-note] details').textContent(), /virtio-win 0\.1\.xxx/);
    await page.locator('[data-version-year]').selectOption('2012');
    assert.equal(await visible().count(), 1);
    assert(await legacy.isVisible());
    await page.reload();
    assert(await legacy.isVisible());
    await assertAvailableYears(); // A selected year must not hide other available years.
    await page.locator('[data-version-year]').selectOption('2023');
    assert.equal(await visible().count(), 1);
    await page.reload();
    assert.equal(await visible().count(), 1);
    await go('?project=virtio&topic=vfio&view=detailed');
    assert.equal(await page.locator('[data-version-topic]').inputValue(), '');
    assert.equal(await visible().count(), specCount);
    assert.equal(await visible().locator('a[href$=".pdf"]:visible').count(), specCount);
    assert.equal(await visible().locator('.version-notes:visible').count(), specCount);
    const noteCounts = await visible().evaluateAll(items => items.map(item => item.querySelectorAll('.version-notes li').length));
    assert(noteCounts.every(count => count >= 2 && count <= 3));
    assert.match(await page.locator('[id="release-virtio-1.4-cs01"] .version-detail').textContent(), /device parts/);
    await go('#virtio-specifications');
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'virtio');
    assert.equal(await visible().count(), specCount);
    await page.locator('[data-version-jump][href^="#release-qemu-"]').click();
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'qemu');
    await page.locator('[data-version-jump][href^="#release-virtio-"]').click();
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'virtio');
    await go('');
    await page.locator('[data-version-project-link="virtio"]').click();
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'virtio');
    await page.locator('[data-version-project]').selectOption('');
    await assertAvailableYears();
    assert.match(await page.locator('[data-version-count]').textContent(), /个功能版本 · \d+ 份规范文档/);

    // Latest links and the project selector share the same six-project order.
    assert.deepEqual(await page.locator('.version-latest [data-version-jump] strong').allTextContents(),
      ['QEMU', 'KVM', 'Libvirt', 'Virtio', 'DPDK', 'SPDK']);
    assert.deepEqual(await page.locator('[data-version-project] option').evaluateAll(
      options => options.map(option => option.value)), ['', 'qemu', 'kvm', 'libvirt', 'virtio', 'dpdk', 'spdk']);
    assert.equal(await page.locator('[data-version-protocol]').count(), 0);

    // Themes apply to the three curated projects and the cross-project view.
    for (const project of ['all', 'qemu', 'kvm', 'libvirt']) {
      await go(`?project=${project}&topic=migration`);
      assert(await page.locator('label[for="version-topic"]').isVisible());
      assert.equal(await page.locator('[data-version-topic]').inputValue(), 'migration');
      await assertVisible('migration', project === 'all' ? '' : project);
    }
    for (const project of ['virtio', 'dpdk', 'spdk']) {
      for (const viaUrl of [false, true]) {
        if (viaUrl) await go(`?project=${project}&year=2026&topic=migration`);
        else {
          await go('?project=qemu&year=2025&topic=migration');
          await page.locator('[data-version-project]').selectOption(project);
        }
        assert(await page.locator('label[for="version-topic"]').isHidden());
        assert.equal(await page.locator('[data-version-topic]').inputValue(), '');
        assert(!new URL(page.url()).searchParams.has('topic'));
        assert.equal(await page.locator('[data-version-topic-description]:visible').count(), 0);
        const rows = await visible().evaluateAll(items => items.map(item => ({...item.dataset})));
        assert(rows.length > 0 && rows.every(row => row.project === project));
        await assertAvailableYears();
        await page.reload();
        assert(await page.locator('label[for="version-topic"]').isHidden());
        assert(await visible().count() > 0);
      }
      await page.locator('[data-version-project]').selectOption('qemu');
      assert(await page.locator('label[for="version-topic"]').isVisible());
      assert.equal(await page.locator('[data-version-topic]').inputValue(), '');
    }

    // Changing project preserves supported years and drops unsupported ones.
    await go('?project=qemu&year=2025&view=detailed');
    await page.locator('[data-version-project]').selectOption('virtio');
    assert.equal(await page.locator('[data-version-year]').inputValue(), '');
    assert(!new URL(page.url()).searchParams.has('year'));
    assert.equal(await visible().count(), specCount);
    await assertAvailableYears();
    await go('?project=qemu&year=2023');
    await page.locator('[data-version-project]').selectOption('virtio');
    assert.equal(await page.locator('[data-version-year]').inputValue(), '2023');
    assert.equal(await visible().count(), 1);
    await assertAvailableYears();
    await go('?project=virtio&year=2025');
    assert.equal(await page.locator('[data-version-year]').inputValue(), '');
    assert.equal(await visible().count(), specCount);
    await assertAvailableYears();

    // Backend frameworks retain their software release timelines.
    for (const project of ['dpdk', 'spdk']) {
      await go(`?project=${project}&year=2026&view=detailed`);
      const rows = await visible().evaluateAll(items => items.map(item => ({...item.dataset})));
      assert(rows.length >= 2);
      assert(rows.every(row => row.project === project && row.kind === 'feature'));
      await assertAvailableYears();
      assert(await page.locator('label[for="version-year"]').isVisible());
    }
    // Old reference links fall back to the normal browser without an empty panel.
    await go('?project=vhost-user#protocol-vhost-user');
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'qemu');
    assert(await visible().count() > 0);
    await page.locator('[data-version-jump][href^="#release-dpdk-"]').click();
    assert.equal(await page.locator('[data-version-project]').inputValue(), 'dpdk');
    assert(await page.locator('.version-view').isVisible());

    for (const width of [320, 375, 390, 768, 1366]) {
      await page.setViewportSize({width, height: 900});
      for (const topic of ['migration', 'vfio', 'protocol']) {
        await go(topic === 'protocol' ? '?project=virtio&view=detailed' : `?project=all&topic=${topic}&view=detailed`);
        await assertAvailableYears();
        const dimensions = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth]);
        assert.equal(dimensions[0], dimensions[1], `horizontal overflow in ${topic} at ${width}px`);
        if (topic === 'protocol') {
          await page.locator('[data-version-spec-note] summary').click();
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width,
            `expanded protocol explanation should fit at ${width}px`);
          assert.equal(await visible().count(), specCount);
          const linksAligned = await visible().evaluateAll(items => items.every(item => {
            const [official, pdf] = [...item.querySelectorAll('.version-actions a')].map(link => link.getBoundingClientRect());
            const body = item.querySelector('.version-body').getBoundingClientRect();
            return Math.abs(official.top - pdf.top) < 1 && pdf.left > official.right
              && Math.abs(pdf.right - body.right) < 1;
          }));
          assert(linksAligned, `specification and PDF links should share a right-aligned row at ${width}px`);
          await page.screenshot({path: path.join(shots, `virtio-versions-${width}.png`)});
        } else await assertVisible(topic);
      }
      await go('?project=dpdk&topic=migration&view=detailed');
      assert(await page.locator('label[for="version-topic"]').isHidden());
      assert(await visible().count() > 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width);
      await page.screenshot({path: path.join(shots, `dpdk-versions-${width}.png`)});
    }
    assert.deepEqual(errors, []);
    console.log('Version filters, pagination, deep links, empty states and five viewport widths passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
