/* A fixed-page DMA write and its eventual synchronized teardown. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-iommu-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const LAST_STEP = 11;
  const SCENARIOS = Object.freeze(['cold', 'warm', 'readonly', 'missing']);
  function frame(scenario = 'cold', index = 0) {
    if (!SCENARIOS.includes(scenario)) throw new RangeError('unknown IOMMU scenario');
    if (!Number.isInteger(index) || index < 0 || index > LAST_STEP) throw new RangeError('invalid IOMMU step');
    const missing = scenario === 'missing', readonly = scenario === 'readonly', warm = scenario === 'warm';
    const denied = missing || readonly;
    if (denied && index > 5) return frame(scenario, 5);
    const fault = denied && index === 5;
    const stages = [
      ['准备接收', missing ? '设备已经接入地址空间，但目标页没有映射。接下来的写请求将检查这一条件。' : `设备已接入地址空间，目标页${readonly ? '仅允许设备读' : '允许设备读写'}。${warm ? 'IOTLB 已有有效转换，来自此前对该页的访问。' : 'IOTLB 暂无该页转换。'}`, ''],
      ['设备发出 DMA 写', '网卡发出 Requester ID 0x0300、IOVA 0x00400080 的 64 B 写请求。设备请求没有 QEMU 的用户虚拟地址字段。', 'device'],
      ['选择设备上下文', '本例传统 VT-d 模式按 Bus 3 与 DevFn 0 选择上下文，取得对应地址空间的页表根与 Domain ID。上下文本身也可能命中硬件缓存。', 'iommu'],
      [warm ? 'IOTLB 命中' : 'IOTLB 未命中', warm ? '缓存已经包含页基址、目标物理页与权限，本次无需重新遍历该映射的页表。' : '没有找到可复用的缓存项，硬件继续遍历 I/O 页表。缓存未命中本身不表示访问故障。', 'iommu'],
      [warm ? '取得缓存的转换与权限' : '读取 I/O 页表叶子', missing ? '页表遍历没有得到有效叶子，无法为目标地址提供转换。' : readonly ? '页表叶子有效，目标 HPA 为 0x21000000，但权限只有设备 READ。' : warm ? '复用已缓存的 0x00400000 → 0x21000000，权限为 READ / WRITE。' : '有效叶子把页映射到 HPA 0x21000000，并允许设备写；硬件可缓存这个转换。', 'iommu'],
      [fault ? 'DMA 访问被阻止' : '转换与写权限检查通过', fault ? missing ? '目标页未映射，不能执行本次 DMA。目标缓冲区不变，故障处理依赖设备和平台。' : '设备写不满足只读权限，IOMMU 阻止目标访问。仅有有效地址不足以允许写入。' : '64 B 请求完全位于页内，保留偏移 0x80，目标为 HPA 0x21000080。', 'iommu'],
      ['数据写入后备页', '网卡的 64 B 数据 B 写入 HPA 0x21000080，客户机缓冲区随之更新。此次硬件地址转换无需执行 QEMU 的 map ioctl。', 'memory'],
      ['接收完成', '设备按其协议报告完成，驱动依照同步要求处理缓冲区。宿主映射继续有效，可供后续接收复用。', 'device'],
      ['最终停用该区域', '在后续区域移除或设备退出时，控制方停止相关队列的新请求，并确认设备对这块内存的在途访问已经结束。', 'device'],
      ['UNMAP 内部：清除页表', '内核清除页表映射并收集失效范围。此时尚未完成同步 UNMAP；设备已停止使用该区域，后备页仍不能交给新的使用者。', 'iommu'],
      ['UNMAP 内部：失效完成', '内核提交所需 IOTLB 失效并等待完成。旧转换不再可用。本例没有 ATS 设备侧转换缓存。', 'iommu'],
      ['释放后备页引用', '映射管理代码释放持有的后备页引用，撤销操作完成。满足其他使用者的生命周期约束后，内存才能重新分配。', 'memory']
    ];
    const [title, description, active] = stages[index];
    return {
      scenario, index, title, description, active, fault,
      phase: fault ? 'fault' : index >= 8 ? 'teardown' : index === 7 ? 'received' : 'transfer',
      mapped: !missing && index < 9, writable: !denied,
      cached: !missing && index < 10 && (warm || index >= 4),
      contextSelected: index >= 2, checked: index >= 5,
      cacheHit: index >= 3 && warm, walked: index >= 4 && !warm,
      dmaWritten: index >= 6 && !fault, received: index >= 7 && !fault,
      quiesced: index >= 8, invalidated: index >= 10, released: index === 11,
      memory: index >= 6 && !fault ? 'B' : 'A',
      payloadBytes: index >= 6 && !fault ? 64 : 0,
      target: index >= 5 && !fault ? 0x21000080 : null,
      done: fault || index === LAST_STEP
    };
  }
  function step(state) { return state.done ? frame(state.scenario, state.index) : frame(state.scenario, state.index + 1); }
  function back(state) { return frame(state.scenario, Math.max(0, state.index - 1)); }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-iommu-${name}]`);
    const select = get('scenario');
    let state = frame(select.value);
    function paint() {
      lab.dataset.phase = state.phase;
      get('status').textContent = `第 ${state.index} 步 · ${state.title}`;
      get('title').textContent = state.title;
      get('description').textContent = state.description;
      get('device').textContent = state.quiesced ? '本区域已停用，无在途访问' : state.received ? '正常接收完成' : state.fault ? '目标 DMA 访问失败' : state.index >= 1 ? '已发出 DMA 写请求' : '等待接收';
      get('context').textContent = state.contextSelected ? '已选定本例地址空间与页表根' : '设备上下文 → 本例地址空间';
      get('pte').textContent = !state.mapped ? state.index >= 9 ? '已清除映射' : '无有效叶子' : `HPA 0x21000000 · ${state.writable ? 'R / W' : '仅 R'}`;
      get('cache').textContent = state.cached ? `0x00400000 → 0x21000000 · ${state.writable ? 'R / W' : '仅 R'}${state.index === 9 ? ' · 待同步失效' : ''}` : state.invalidated ? '旧转换已完成失效' : '该页暂无有效缓存';
      get('verdict').textContent = state.fault ? state.scenario === 'missing' ? '访问故障：未映射' : '访问故障：缺少写权限' : state.invalidated ? '旧映射已撤销' : state.checked ? '本次请求获准 → HPA 0x21000080' : '等待访问检查';
      get('memory').textContent = state.memory === 'B' ? '已接收数据 B' : '原有数据 A';
      get('owner').textContent = state.released ? '映射引用已释放；其他使用者仍须结束使用' : state.scenario === 'missing' ? '目标页未向设备映射' : state.index >= 9 ? '等待撤销完成，暂不可复用' : '映射持有后备页引用';
      lab.querySelectorAll('[data-iommu-node]').forEach(node => { node.dataset.active = String(node.dataset.iommuNode === state.active); });
      get('back').disabled = state.index === 0;
      get('next').disabled = state.done;
    }
    function reset() { state = frame(select.value); paint(); }
    get('next').addEventListener('click', () => { state = step(state); paint(); });
    get('back').addEventListener('click', () => { state = back(state); paint(); });
    get('reset').addEventListener('click', reset);
    select.addEventListener('change', reset);
    get('controls').hidden = false;
    get('status').hidden = false;
    get('detail').hidden = false;
    get('fallback').hidden = true;
    paint();
    return {getState: () => state};
  }
  return {LAST_STEP, SCENARIOS, frame, step, back, mount};
});
