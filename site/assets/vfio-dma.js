/* One ordinary PCIe DMA transaction, with setup and hardware access separated. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-vfio-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const LAST_STEP = 8;
  const MAP = Object.freeze({ioasId: 10, hwptId: 20, iova: 0x00400000, userVa: 0x70001000, length: 4096});
  const HOST_PAGE = 0x21000000;
  const REQUEST = Object.freeze({iova: 0x00400080, length: 64});
  function normalizeConfig(config = {}) {
    const permission = config.permission || 'rw', operation = config.operation || 'read';
    if (!['rw', 'readonly', 'missing'].includes(permission)) throw new RangeError('unknown mapping');
    if (!['read', 'write'].includes(operation)) throw new RangeError('unknown DMA direction');
    return {permission, operation};
  }
  function checkAccess(mapping, request) {
    if (!mapping) return {allowed: false, reason: 'unmapped', hpa: null};
    if (!['read', 'write'].includes(request.operation)) throw new RangeError('unknown DMA direction');
    if (!Number.isSafeInteger(request.iova) || !Number.isSafeInteger(request.length) || request.length <= 0 ||
        request.iova < mapping.iova || request.iova + request.length > mapping.iova + mapping.length) {
      return {allowed: false, reason: 'range', hpa: null};
    }
    if (!mapping[request.operation]) return {allowed: false, reason: 'permission', hpa: null};
    return {allowed: true, reason: '', hpa: mapping.hpa + (request.iova - mapping.iova)};
  }
  function frame(config, index) {
    const options = normalizeConfig(config);
    if (!Number.isInteger(index) || index < 0 || index > LAST_STEP) throw new RangeError('invalid stage');
    const missing = options.permission === 'missing';
    const mapping = missing || index < 3 ? null : {...MAP, hpa: HOST_PAGE, read: true, write: options.permission === 'rw'};
    const request = {...REQUEST, operation: options.operation};
    const verdict = index >= 6 ? checkAccess(mapping, request) : null;
    const fault = verdict !== null && !verdict.allowed;
    // A blocked request is terminal; neither data nor successful completion follows it.
    if (fault && index > 6) return frame(options, 6);
    const read = options.operation === 'read';
    const stages = [
      ['准备', '建立设备所有权、附着地址空间并设置目标页映射后，开始处理一个 DMA 请求。', ''],
      ['绑定 IOMMUFD', 'VFIO_DEVICE_BIND_IOMMUFD 检查 group 的 DMA 所有权，并把设备绑定到 IOMMUFD 上下文。此时尚未附着 I/O 页表。', 'control'],
      ['附着 I/O 地址空间', 'VFIO_DEVICE_ATTACH_IOMMUFD_PT 指定 IOAS 10；内核为设备复用兼容或新建的 HWPT，本例标为 HWPT 20。目标页尚未映射。', 'control'],
      [missing ? '保留映射空洞' : '设置目标页映射', missing ? '本例不映射 0x00400000 所在页。设备已经附着地址空间，但没有访问这块页的转换。' : `IOMMU_IOAS_MAP 提交 IOAS、IOVA、user_va、长度及权限。内核固定后备页，把 IOVA 0x00400000 映射到 HPA 0x21000000；权限为${options.permission === 'rw' ? '设备读写' : '设备只读'}。`, 'control'],
      ['客户机提交请求', `客户机驱动提交 GPA 0x00400080、长度 64 B 的缓冲区，本次要求设备${read ? '读取' : '写入'}。设备收到的 DMA 地址采用相同数值的 IOVA。`, 'guest'],
      ['设备发出 DMA', `物理设备发出${read ? '读' : '写'}请求：IOVA 0x00400080，长度 64 B。设备标识选择已附着的 IOMMU 转换上下文；请求没有 QEMU HVA 字段。`, 'device'],
      [fault ? '访问被阻止' : '转换与权限检查通过', fault ? missing ? '目标 IOVA 没有有效映射，产生 IOMMU 访问故障。请求不能访问后备页，也不进入正常数据完成阶段。' : '该页仅允许设备读取，本次设备写入不满足权限。IOMMU 阻止目标数据写入，缓冲区内容保持不变。' : '页内偏移为 0x80，64 B 请求完整落在已映射页内，方向满足权限，目标地址为 HPA 0x21000080。', 'iommu'],
      ['执行数据访问', read ? '设备从 HPA 0x21000080 读取 64 B 数据 A；客户机后备页内容保持不变。' : '设备向 HPA 0x21000080 写入 64 B 数据 B；客户机后备页的对应缓冲区更新。', 'memory'],
      ['正常请求完成', read ? '设备已取得数据，按本例设备协议报告正常完成；驱动可以回收发送缓冲区。' : '设备已写入数据并报告正常完成；驱动按设备协议与同步要求读取新的缓冲区内容。', 'guest']
    ];
    const [title, detail, active] = stages[index];
    const accessed = index >= 7 && !fault;
    return {
      config: options, index, title, detail, active, mapping, request, verdict,
      bound: index >= 1, attached: index >= 2, mapProcessed: index >= 3,
      submitted: index >= 4, issued: index >= 5, checked: index >= 6,
      fault, accessed, completed: index === LAST_STEP && !fault,
      phase: fault ? 'fault' : index === LAST_STEP ? 'done' : index <= 3 ? 'setup' : 'transfer',
      memory: accessed && !read ? 'B' : 'A', deviceData: accessed && read ? 'A' : null,
      payloadBytes: accessed ? REQUEST.length : 0,
      cpuTranslationsPerRequest: 0,
      history: stages.slice(0, index + 1).map((stage, number) => ({index: number, title: stage[0]}))
    };
  }
  function createState(config) { return frame(config, 0); }
  function step(state) { return frame(state.config, Math.min(state.fault ? 6 : LAST_STEP, state.index + 1)); }
  function back(state) { return frame(state.config, Math.max(0, state.index - 1)); }
  function hex(value) { return `0x${value.toString(16).padStart(8, '0')}`; }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-vfio-${name}]`);
    const permission = get('permission'), operation = get('operation');
    let state = createState({permission: permission.value, operation: operation.value});
    function paint() {
      lab.dataset.phase = state.phase;
      const read = state.config.operation === 'read', missing = state.config.permission === 'missing';
      get('status').textContent = `第 ${state.index} 步 · ${state.title}`;
      get('step-title').textContent = state.title; get('step-detail').textContent = state.detail;
      get('association').textContent = state.attached ? '设备 → HWPT 20；HWPT 20 对应 IOAS 10 的映射' : state.bound ? '设备已绑定 IOMMUFD，尚未附着 IOAS / HWPT' : '设备尚未绑定 IOMMUFD';
      get('map-state').textContent = !state.mapProcessed ? '等待建立目标页映射' : missing ? '目标 IOVA 页无映射' : '已提交 IOVA + HVA + 长度 + 权限，内核完成映射';
      get('map-flags').textContent = missing ? '本例不发出目标页 MAP' : `FIXED_IOVA | READABLE${state.config.permission === 'rw' ? ' | WRITEABLE' : ''}`;
      get('mapped-hpa').textContent = state.mapping ? hex(state.mapping.hpa) : state.mapProcessed ? '无映射' : '等待内核建立映射';
      get('target').textContent = state.mapping ? hex(state.mapping.hpa) : '未映射';
      get('rights').textContent = !state.mapping ? '无' : state.mapping.write ? 'READ / WRITE' : 'READ';
      get('driver').textContent = state.completed ? '正常请求完成，驱动可处理缓冲区' : state.submitted ? '已提交：GPA 0x00400080，64 B' : '缓冲区 GPA 0x00400080，长度 64 B';
      get('request-direction').textContent = read ? 'READ · 设备读' : 'WRITE · 设备写';
      get('direction').textContent = read ? '发出读请求' : '发出写请求';
      get('device-state').textContent = state.fault ? '目标数据访问失败' : state.completed ? '正常请求完成' : state.accessed ? read ? '已取得数据 A' : '已写入数据 B' : state.issued ? '已发出 DMA，等待检查' : state.submitted ? '已收到客户机请求' : '等待请求';
      get('verdict').textContent = !state.checked ? '等待检查映射及权限' : state.fault ? missing ? '故障：缺少映射' : '故障：写入权限不足' : `允许 → ${hex(state.verdict.hpa)}`;
      get('data-arrow').textContent = read ? '←' : '→';
      get('data-label').textContent = state.fault ? '目标访问被阻止' : state.accessed ? `已${read ? '读取' : '写入'} 64 B` : '64 B 数据';
      get('memory-value').textContent = state.memory === 'A' ? '原有数据 A' : '新数据 B';
      get('access').textContent = state.accessed ? read ? '已被设备读取，内容不变' : '设备写入已生效' : state.fault ? '未发生目标数据访问' : '尚未发生 DMA 数据访问';
      get('completion').hidden = !state.completed;
      lab.querySelectorAll('[data-vfio-node]').forEach(node => { node.dataset.active = String(node.dataset.vfioNode === state.active); });
      lab.querySelectorAll('[data-vfio-edge]').forEach(node => { node.dataset.active = String(node.dataset.vfioEdge === (state.index === 5 ? 'request' : state.index === 7 ? 'data' : '')); });
      get('next').disabled = state.completed || state.fault; get('back').disabled = state.index === 0;
    }
    function reset() { state = createState({permission: permission.value, operation: operation.value}); paint(); }
    get('next').addEventListener('click', () => { state = step(state); paint(); });
    get('back').addEventListener('click', () => { state = back(state); paint(); });
    get('reset').addEventListener('click', reset);
    permission.addEventListener('change', reset); operation.addEventListener('change', reset);
    get('controls').hidden = false; get('status').hidden = false; get('detail').hidden = false; get('fallback').hidden = true;
    paint();
    return {getState: () => state};
  }
  return {MAP, HOST_PAGE, REQUEST, LAST_STEP, normalizeConfig, checkAccess, frame, createState, step, back, mount};
});
