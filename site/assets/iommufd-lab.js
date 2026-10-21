/* Single-device IOMMUFD ownership and explicit HWPT lifecycle. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-iommufd-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const MODES = Object.freeze(['success', 'attach-failure', 'map-failure']);

  function buildRun(mode = 'success') {
    if (!MODES.includes(mode)) throw new RangeError('unknown IOMMUFD scenario');
    const frames = [];
    let state = {mode, deviceFd: false, contextFd: false, bound: false, ioas: false, hwpt: false,
      attached: false, mapped: false, unmapped: false, dmaRunning: false, dmaRequests: 0, quiesced: false,
      fault: false, released: false};
    function add(key, title, command, detail, changes = {}) {
      state = {...state, ...changes, key, title, command, detail, index: frames.length};
      frames.push(state);
    }
    add('prepare', '准备接入', '尚未发出 ioctl', '从两个文件描述符开始，逐步建立设备、页表和 RAM 映射。');
    add('open', '打开设备与 IOMMUFD', 'open(devfd) + open(/dev/iommu)', '已取得两个 fd。VFIO cdev 尚未绑定 IOMMUFD，设备接口访问仍受限制。', {deviceFd: true, contextFd: true});
    add('bind', '建立 DMA 所有权', 'VFIO_DEVICE_BIND_IOMMUFD → DEVICE 1', 'VFIO 检查所有权并创建绑定，返回 dev_id。此时尚未附着硬件页表，也没有 RAM 映射。', {bound: true});
    add('ioas', '分配逻辑地址空间', 'IOMMU_IOAS_ALLOC → IOAS 10', '空 IOAS 负责保存 IOVA 与后备内存的对应关系。', {ioas: true});
    add('hwpt', '分配硬件页表', 'IOMMU_HWPT_ALLOC(dev_id=1, pt_id=10) → HWPT 20', '为本例设备分配 HWPT，并关联 IOAS 10。设备尚未附着到这张页表。', {hwpt: true});
    if (mode === 'attach-failure') {
      add('attach-failure', '页表附着失败', 'VFIO_DEVICE_ATTACH_IOMMUFD_PT → 失败返回', '本场景模拟附着 ioctl 失败。QEMU 没有得到成功的关联，保留已创建对象并进入回滚；没有 RAM 映射，也没有启动 DMA。', {fault: true});
    } else {
      add('attach', '设备附着到 HWPT', 'VFIO_DEVICE_ATTACH_IOMMUFD_PT(pt_id=20)', '设备现在使用 HWPT 20，但目标 RAM 区间仍未映射，尚不能访问该区间。', {attached: true});
      if (mode === 'map-failure') {
        add('map-failure', 'RAM 映射被拒绝', 'IOMMU_IOAS_MAP → 地址范围校验失败', '本场景假设目标 IOVA 落在当前设备不允许使用的区间，校验失败，未建立该映射。设备尚未开始 DMA，接下来撤销附着并释放对象。', {fault: true});
      } else {
        add('map', '建立 RAM 映射', 'IOMMU_IOAS_MAP(ioas_id=10)', '映射 2 MiB RAM，允许设备读写。内核维护后备页引用，并将 IOVA 映射同步到 HWPT。', {mapped: true});
        add('dma', '设备使用已建立的映射', '设备 DMA → IOMMU → RAM', '客户机提交缓冲区，网卡通过已关联页表访问 RAM。本步示意一笔正常 DMA；数据包不需要逐个调用 MAP。', {dmaRunning: true, dmaRequests: 1});
        add('stop', '停止设备并处理在途 DMA', '停止队列 → 等待设备完成 / 停止', '不再提交新请求，并按设备协议确认在途 DMA 已处理。现在可以回收该设备使用的地址空间。', {dmaRunning: false, quiesced: true});
      }
      add('detach', '解除设备与页表关联', 'VFIO_DEVICE_DETACH_IOMMUFD_PT', '设备脱离 HWPT。此操作不会删除 IOAS 中已经存在的映射。', {attached: false});
    }
    add('destroy-hwpt', '释放无设备使用的 HWPT', 'IOMMU_DESTROY(id=20)', '本例只有一块设备，脱离后 HWPT 不再被设备使用，可以销毁；IOAS 仍然存在。', {hwpt: false});
    if (mode === 'success') {
      add('unmap', '撤销 RAM 映射', 'IOMMU_IOAS_UNMAP(ioas_id=10)', '从 IOAS 撤销映射并释放相应的页引用。IOAS 对象本身仍然存在。', {mapped: false, unmapped: true});
    }
    add('destroy-ioas', '释放 IOAS', 'IOMMU_DESTROY(id=10)', '没有 HWPT 和映射需要保留，销毁本次创建的 IOAS。设备绑定仍由 VFIO 设备 fd 持有。', {ioas: false});
    add('close-device', '关闭设备并解除绑定', 'close(devfd)', '关闭设备 fd，VFIO 执行解绑并释放设备持有的 IOMMUFD 上下文引用。', {bound: false, deviceFd: false});
    add('close-context', '关闭 IOMMUFD', 'close(iommufd)', '本例对象、映射和绑定均已回收，最后关闭用户态 IOMMUFD 文件描述符。', {contextFd: false, released: true});
    return frames;
  }
  function frame(mode, index) {
    const frames = buildRun(mode);
    if (!Number.isInteger(index) || index < 0 || index >= frames.length) throw new RangeError('invalid lifecycle step');
    return {...frames[index], total: frames.length};
  }
  function step(state) { return frame(state.mode, Math.min(state.index + 1, state.total - 1)); }
  function back(state) { return frame(state.mode, Math.max(0, state.index - 1)); }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-iommufd-${name}]`);
    const scenario = get('scenario');
    let state = frame(scenario.value, 0);
    function paint() {
      lab.dataset.fault = String(state.fault && !state.released);
      get('status').textContent = `第 ${state.index} / ${state.total - 1} 步 · ${state.title}`;
      get('step-title').textContent = state.title;
      get('command').textContent = state.command;
      get('step-detail').textContent = state.detail;
      get('device-fd').textContent = state.deviceFd ? state.bound ? '已打开 · 已绑定' : '已打开 · 待绑定' : state.index ? '已关闭' : '尚未打开';
      get('context-fd').textContent = state.contextFd ? '已打开' : state.index ? '已关闭' : '尚未打开';
      get('device-state').textContent = state.bound ? '已绑定 IOMMUFD' : state.index > 2 ? '已解绑' : '未绑定';
      get('hwpt-state').textContent = state.hwpt ? state.attached ? '已分配 · 设备已附着' : '已分配 · 未附着设备' : state.index > 4 ? '已释放' : '未分配';
      get('ioas-state').textContent = state.ioas ? state.mapped ? '已分配 · 1 段映射' : '已分配 · 无映射' : state.index > 3 ? '已释放' : '未分配';
      get('attachment').textContent = state.attached ? '已附着' : state.key === 'attach-failure' ? '附着失败' : '无附着';
      get('association').textContent = state.hwpt ? '关联 IOAS' : '无关联';
      get('mapping-state').textContent = state.mapped ? '已建立 · 后备页受管理' : state.unmapped ? '已撤销 · 无页引用' : state.fault && state.mode === 'map-failure' ? '校验失败 · 未建立' : state.released ? '无映射 · 无页引用' : '尚未建立';
      get('dma').textContent = state.dmaRunning ? '数据路径：网卡 VF → IOMMU → RAM' : state.quiesced ? '设备已停止，本例完成 1 笔 DMA。' : state.fault ? '初始化未完成，未启动 DMA。' : '尚未启动 DMA。';
      const present = {device: state.bound, hwpt: state.hwpt, ioas: state.ioas, mapping: state.mapped};
      lab.querySelectorAll('[data-iommufd-object]').forEach(node => { node.dataset.present = String(present[node.dataset.iommufdObject]); });
      get('next').disabled = state.released;
      get('back').disabled = state.index === 0;
    }
    function reset() { state = frame(scenario.value, 0); paint(); }
    get('next').addEventListener('click', () => { state = step(state); paint(); });
    get('back').addEventListener('click', () => { state = back(state); paint(); });
    get('reset').addEventListener('click', reset);
    scenario.addEventListener('change', reset);
    get('controls').hidden = false;
    get('status').hidden = false;
    get('detail').hidden = false;
    get('fallback').hidden = true;
    paint();
    return {getState: () => state};
  }
  return {MODES, buildRun, frame, step, back, mount};
});
