/* A single IOVA through guest PTE changes, shadow maps and host IOTLB. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-viommu-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const MODES = Object.freeze(['correct', 'missing-invalidation']);
  const PAGES = Object.freeze({
    A: Object.freeze({gpa: '0x40000000', hpa: '0x120000000', destination: '0x120000080'}),
    B: Object.freeze({gpa: '0x50000000', hpa: '0x140000000', destination: '0x140000080'})
  });
  function dma(state) {
    const page = state.cache || state.host;
    return {page, cacheHit: Boolean(state.cache), blocked: page === null,
      destination: page ? PAGES[page].destination : null,
      wrongOwner: page === 'A' && state.aReused};
  }
  function buildRun(mode = 'correct') {
    if (!MODES.includes(mode)) throw new RangeError('unknown vIOMMU scenario');
    const frames = [];
    let state = {mode, guest: null, host: null, cache: null, aReused: false,
      pending: false, invalidated: false, oldRequestDone: false, corrupted: false,
      writesA: 0, writesB: 0, result: null, done: false};
    function add(key, title, detail, changes = {}) {
      state = {...state, ...changes, key, title, detail, index: frames.length};
      frames.push(state);
    }
    function access(key, title, detail) {
      const result = dma(state);
      add(key, title, detail, {result, cache: result.page,
        writesA: state.writesA + (result.page === 'A' ? 1 : 0),
        writesB: state.writesB + (result.page === 'B' ? 1 : 0),
        corrupted: state.corrupted || result.wrongOwner});
    }
    add('prepare', '准备缓冲区 A', 'A 已分配，设备尚未取得它的 DMA 地址。三处转换状态均为空。');
    add('guest-map-a', '客户机写入 A 的页表项', '客户机 PTE 指向 A；尚未同步到宿主。此时不能向设备发布该缓冲区。', {guest: 'A', pending: true});
    add('sync-a', '新增映射完成同步', '客户机失效请求触发 QEMU 扫描，宿主 MAP 成功。硬件页表指向 A；IOTLB 尚未缓存本次转换。', {host: 'A', pending: false});
    access('dma-a', '设备向 A 写入 64 字节', 'DMA 地址为 0x00800080。IOTLB 未命中，宿主页表给出 A 的 HPA，转换结果进入缓存。');
    add('complete-a', '确认 A 的旧请求完成', '按设备协议确认旧请求已结束，再开始撤销缓冲区映射。', {oldRequestDone: true, result: null});
    add('guest-unmap-a', '客户机清除 A 的页表项', '客户机 PTE 已无效，宿主页表和 IOTLB 仍保存 A。客户机的内存写入没有自动撤销后端映射。', {guest: null, pending: true});
    if (mode === 'correct') {
      add('unmap-complete', '撤销映射与失效完成', '客户机发出失效，QEMU 通知 UNMAP，宿主完成页表撤销与转换缓存处理。本步骤表示完成后的状态。', {host: null, cache: null, pending: false, invalidated: true});
    } else {
      add('skip-invalidation', '错误：遗漏客户机失效', '没有提交失效，也没有调用宿主 UNMAP。软件直接继续回收 A，物理设备使用的旧映射仍有效。');
    }
    add('reuse-a', '客户机把 A 改作其他用途', mode === 'correct'
      ? '旧请求和映射撤销均已完成。A 不再是接收缓冲区。'
      : '错误回收：A 已不属于旧缓冲区，但宿主仍允许原 IOVA 访问它。', {aReused: true});
    access('late-probe', '探测一次违规迟到 DMA', mode === 'correct'
      ? '为比较隔离效果，演示故意发出旧地址访问。宿主页表和缓存均无映射，访问被阻止；A 保持不变。'
      : '演示故意发出旧地址访问。IOTLB 命中 A，写入已改作其他用途的页面，产生错误写入。');
    if (mode === 'missing-invalidation') {
      add('repair-invalidation', '补做失效，撤销旧映射', '现在才完成宿主 UNMAP 与缓存处理。后续旧地址访问不再获准，但已经发生的错误写入不会被撤回。', {host: null, cache: null, pending: false, invalidated: true, result: null});
    }
    add('guest-map-b', '将同一 IOVA 映射到 B', '客户机 PTE 改为 GPA 0x50000000；宿主仍没有该 IOVA 的映射，等待新增映射同步。', {guest: 'B', pending: true, result: null});
    add('sync-b', 'B 的新映射完成同步', 'QEMU 读取 B 的 PTE，宿主建立 IOVA→B 的 HPA。现在可以把 B 交给设备。', {host: 'B', pending: false});
    access('dma-b', '相同 DMA 地址写入 B', 'DMA 地址仍是 0x00800080，此时目标为 0x140000080。IOTLB 缓存 B 的转换。');
    state = {...state, done: true};
    frames[frames.length - 1] = state;
    return frames;
  }
  function frame(mode, index) {
    const frames = buildRun(mode);
    if (!Number.isInteger(index) || index < 0 || index >= frames.length) throw new RangeError('invalid vIOMMU step');
    return {...frames[index], total: frames.length};
  }
  function step(state) { return frame(state.mode, Math.min(state.index + 1, state.total - 1)); }
  function back(state) { return frame(state.mode, Math.max(0, state.index - 1)); }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-viommu-${name}]`);
    const scenario = get('scenario');
    let state = frame(scenario.value, 0);
    function mapping(page, address) { return page ? `0x00800000 → ${PAGES[page][address]} · 页 ${page}` : '无映射'; }
    function paint() {
      get('status').textContent = `第 ${state.index} / ${state.total - 1} 步 · ${state.title}`;
      get('detail').textContent = state.detail;
      get('guest').textContent = mapping(state.guest, 'gpa');
      get('host').textContent = mapping(state.host, 'hpa');
      get('cache').textContent = state.cache ? `缓存页 ${state.cache} → ${PAGES[state.cache].hpa}` : '未缓存转换';
      get('sync').textContent = state.pending ? '客户机已改页表 · 后端尚未同步' : '本步无待同步的页表修改';
      get('a-owner').textContent = state.aReused ? '已改作其他用途' : '接收缓冲区 A';
      get('a-writes').textContent = `累计 DMA 写入 ${state.writesA} 次${state.corrupted ? ' · 含 1 次错误写入' : ''}`;
      get('b-writes').textContent = `累计 DMA 写入 ${state.writesB} 次`;
      get('queue').textContent = state.oldRequestDone ? 'A 的原请求已完成；下方探测属于故意模拟的非法访问。' : '原请求完成之前，保留缓冲区和映射。';
      const result = state.result;
      get('route').textContent = !result ? '等待下一笔 DMA' : result.blocked
        ? '设备 → 宿主 IOMMU：无映射，访问被阻止'
        : `设备 → ${result.cacheHit ? 'IOTLB 命中' : '宿主页表遍历'} → 页 ${result.page} · ${result.destination}`;
      lab.dataset.pending = String(state.pending);
      lab.dataset.corrupted = String(state.corrupted);
      lab.querySelectorAll('[data-viommu-page]').forEach(node => {
        node.dataset.active = String(Boolean(result && result.page === node.dataset.viommuPage));
      });
      lab.querySelectorAll('[data-viommu-layer]').forEach(node => {
        node.dataset.present = String(Boolean(state[node.dataset.viommuLayer]));
      });
      get('next').disabled = state.done;
      get('back').disabled = state.index === 0;
    }
    function reset() { state = frame(scenario.value, 0); paint(); }
    get('next').addEventListener('click', () => { state = step(state); paint(); });
    get('back').addEventListener('click', () => { state = back(state); paint(); });
    get('reset').addEventListener('click', reset);
    scenario.addEventListener('change', reset);
    get('controls').hidden = false;
    get('status').hidden = false;
    get('explanation').hidden = false;
    get('fallback').hidden = true;
    paint();
    return {getState: () => state};
  }
  return {MODES, PAGES, dma, buildRun, frame, step, back, mount};
});
