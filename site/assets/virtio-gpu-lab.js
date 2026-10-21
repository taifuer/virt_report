/* Ordinary 2D virtio-gpu: backing, host image, and display consumption. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-virtio-gpu-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const WIDTH = 1024, HEIGHT = 768, BPP = 4, STRIDE = WIDTH * BPP, LAST_STEP = 10;
  function rectangle(size = 'small') {
    if (!['small', 'large'].includes(size)) throw new RangeError('unknown update size');
    const width = size === 'large' ? 256 : 128, height = size === 'large' ? 128 : 64;
    return {x: 128, y: 96, width, height, offset: 96 * STRIDE + 128 * BPP, bytes: width * height * BPP};
  }
  function rowCopies(rect) {
    if (!rect || !['x', 'y', 'width', 'height', 'offset'].every(key => Number.isSafeInteger(rect[key])) ||
        rect.x < 0 || rect.y < 0 || rect.width <= 0 || rect.height <= 0 || rect.offset < 0 ||
        rect.x + rect.width > WIDTH || rect.y + rect.height > HEIGHT ||
        rect.offset + (rect.height - 1) * STRIDE + rect.width * BPP > STRIDE * HEIGHT) {
      throw new RangeError('rectangle outside image or backing');
    }
    return Array.from({length: rect.height}, (_, row) => ({
      source: rect.offset + STRIDE * row,
      destination: (rect.y + row) * STRIDE + rect.x * BPP,
      length: rect.width * BPP
    }));
  }
  function frame(size = 'small', index = 0) {
    const rect = rectangle(size);
    if (!Number.isInteger(index) || index < 0 || index > LAST_STEP) throw new RangeError('invalid stage');
    const steps = [
      ['准备初始画面', '客户机内存已有画面 A。设备端尚无资源，输出也尚未绑定。', 'CPU 写 backing', 'guest'],
      ['创建图像资源', 'RESOURCE_CREATE_2D 为资源 7 分配 pixman 图像。新图像的像素尚未从客户机初始化。', 'RESOURCE_CREATE_2D', 'host'],
      ['关联客户机内存', 'RESOURCE_ATTACH_BACKING 把内存段列表关联到资源 7。QEMU 建立 iov；关联操作没有复制像素。', 'RESOURCE_ATTACH_BACKING', 'host'],
      ['传输完整画面', 'TRANSFER_TO_HOST_2D 将 backing 中的完整画面 A 复制到 pixman 图像，共 3 MiB。', 'TRANSFER_TO_HOST_2D', 'host'],
      ['绑定显示输出', 'SET_SCANOUT 将输出 0 绑定到资源 7 的完整区域。DisplaySurface 引用图像像素，不分配第二份资源副本。显示消费在本演示下一步展开。', 'SET_SCANOUT', 'display'],
      ['提交初始画面更新', 'RESOURCE_FLUSH 提交完整画面更新。本例让显示后端消费这次更新，形成后续比较用的画面 A。', 'RESOURCE_FLUSH', 'display'],
      ['客户机修改矩形', `客户机把 (${rect.x},${rect.y}) 开始的 ${rect.width}×${rect.height} 区域改为 B。此时 pixman 图像仍为 A。`, 'CPU 写 backing', 'guest'],
      ['复制局部像素', `TRANSFER_TO_HOST_2D 从 offset ${rect.offset} 开始，逐行复制 ${rect.height} 行、每行 ${rect.width * BPP} B。QEMU 图像变为 B；显示端的上次消费结果仍为 A。`, 'TRANSFER_TO_HOST_2D', 'host'],
      ['通知显示区域更新', 'RESOURCE_FLUSH 对可见区域求交并提交 dpy_gfx_update。此步没有再次复制 backing，显示后端已有待处理的区域更新。', 'RESOURCE_FLUSH · FENCE = 1', 'display'],
      ['命令处理完成', 'QEMU 返回携带 fence_id = 42 的完成响应，发布 used 记录。该完成不保证显示端已消费更新，更不代表物理屏幕刷新完成。', 'RESP_OK_NODATA · fence_id = 42', 'host'],
      ['显示端消费更新', '显示后端处理此前通知的区域，演示画面更新为 B。真实显示端与命令响应可以采用不同调度顺序；这里明确展示二者独立。', '显示后端处理待更新区域', 'display']
    ];
    const [title, detail, command, active] = steps[index];
    return {
      size, index, rect, title, detail, command, active,
      created: index >= 1, attached: index >= 2, scanout: index >= 4,
      guestVersion: index >= 6 ? 'B' : 'A',
      hostVersion: index >= 7 ? 'B' : index >= 3 ? 'A' : index >= 1 ? 'allocated' : 'none',
      displayVersion: index >= 10 ? 'B' : index >= 5 ? 'A' : 'none',
      copiedBytes: index === 3 ? STRIDE * HEIGHT : index === 7 ? rect.bytes : 0,
      totalCopiedBytes: (index >= 3 ? STRIDE * HEIGHT : 0) + (index >= 7 ? rect.bytes : 0),
      updatePending: index >= 8 && index < 10,
      fenceReturned: index >= 9,
      physicalPresentationKnown: false
    };
  }
  function step(state) { return frame(state.size, Math.min(LAST_STEP, state.index + 1)); }
  function back(state) { return frame(state.size, Math.max(0, state.index - 1)); }
  function bytes(value) { return value === 0 ? '0 B' : value >= 1048576 ? `${value / 1048576} MiB` : `${value / 1024} KiB`; }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-gpu-${name}]`);
    let state = frame(get('size').value);
    function paint() {
      const r = state.rect;
      lab.dataset.size = state.size;
      get('status').textContent = `第 ${state.index} / ${LAST_STEP} 步 · ${state.title}`;
      get('detail').textContent = state.detail;
      get('command').textContent = state.command;
      get('fields').textContent = state.index === 2 ? 'resource_id = 7 · mem_entry[] → res.iov' : state.index === 3 ? 'resource_id = 7 · r = (0,0,1024,768) · offset = 0' : state.index === 4 ? 'scanout_id = 0 · resource_id = 7 · r = (0,0,1024,768)' : state.index >= 7 ? `resource_id = 7 · r = (${r.x},${r.y},${r.width},${r.height})${state.index === 7 ? ` · offset = ${r.offset}` : ''}` : 'resource_id = 7 · 1024×768 · B8G8R8X8_UNORM';
      get('response').textContent = state.fenceReturned ? 'fence_id = 42 已返回；不代表物理呈现完成' : state.index === 8 ? '等待本次 FLUSH 的 fence 响应' : state.index >= 1 && state.index <= 5 ? '本演示已完成当前初始化操作' : state.index === 7 ? '像素复制已完成' : '本步尚无新的命令完成';
      get('guest-state').textContent = state.guestVersion === 'B' ? '矩形已改为 B，其余仍为 A' : '初始画面 A';
      get('host-state').textContent = state.hostVersion === 'none' ? '尚未创建资源' : state.hostVersion === 'allocated' ? '图像已分配，像素待初始化' : state.hostVersion === 'B' ? '矩形已复制为 B' : '已保存画面 A';
      get('backing-state').textContent = state.attached ? 'backing 已关联 · iov → 客户机内存' : 'backing 尚未关联';
      get('display-state').textContent = state.displayVersion === 'B' ? '已消费局部更新 B' : state.displayVersion === 'A' ? state.updatePending ? '仍示意上次画面 A · 更新待消费' : '上次消费的画面 A' : state.scanout ? '已接入表面 · 尚未展开显示消费' : '输出尚未接入';
      get('scanout-state').textContent = state.scanout ? 'scanout 0 → resource 7 · 表面引用图像' : '等待 SET_SCANOUT';
      get('bytes').textContent = bytes(state.copiedBytes);
      get('rect').textContent = `${r.width} × ${r.height} · offset ${r.offset} B`;
      get('notify').textContent = state.updatePending ? '局部更新待显示端消费' : state.index >= 10 ? '本例局部更新已消费' : state.index >= 5 ? '初始更新已消费' : '尚未发出';
      for (const name of ['guest', 'host', 'display']) {
        lab.querySelector(`[data-gpu-image="${name}"]`).dataset.version = state[`${name}Version`];
        lab.querySelector(`[data-gpu-node="${name}"]`).dataset.active = String(state.active === name);
      }
      get('copy').dataset.active = String(state.index === 3 || state.index === 7);
      get('flush').dataset.active = String(state.index === 5 || state.index === 8);
      get('back').disabled = state.index === 0;
      get('next').disabled = state.index === LAST_STEP;
    }
    function reset() { state = frame(get('size').value); paint(); }
    get('next').addEventListener('click', () => { state = step(state); paint(); });
    get('back').addEventListener('click', () => { state = back(state); paint(); });
    get('reset').addEventListener('click', reset);
    get('size').addEventListener('change', reset);
    get('controls').hidden = false; get('status').hidden = false; get('fallback').hidden = true;
    paint();
    return {getState: () => state};
  }
  return {WIDTH, HEIGHT, BPP, STRIDE, LAST_STEP, rectangle, rowCopies, frame, step, back, mount};
});
