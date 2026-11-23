/* A single GPU VF: guest RAM travels separately from framebuffer and device state. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-vfio-migration-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const MODES = Object.freeze(['precopy', 'stopcopy', 'cancel']);

  function buildRun(mode = 'precopy') {
    if (!MODES.includes(mode)) throw new RangeError('unknown VFIO migration scenario');
    const precopy = mode !== 'stopcopy';
    const frames = [];
    let state = {mode, sourceCpu: true, targetCpu: false, sourceDma: true, targetDma: false,
      sourceDevice: 'RUNNING', targetDevice: 'STOP', sourceRam: [1, 1, 1, 1], targetRam: [0, 0, 0, 0],
      sourceVram: [1, 1], targetVram: [0, 0],
      sourceState: '当前状态 · 持续更新', targetState: '尚未加载', initialLoaded: false,
      ack: false, ramFinal: false, deviceFinal: false, validated: false, authority: 'source',
      transferRam: false, transferDevice: false, stopped: false, cancelled: false, done: false};
    function add(key, title, action, detail, changes = {}) {
      state = {...state, ...changes, key, title, action, detail, index: frames.length};
      state.sourceRam = [...state.sourceRam]; state.targetRam = [...state.targetRam];
      state.sourceVram = [...state.sourceVram]; state.targetVram = [...state.targetVram];
      frames.push(state);
    }
    add('running', '源 GPU 正在执行计算', '源 VF：RUNNING；目的 VF：STOP', '客户机向 GPU 提交任务，计算结果留在显存，完成记录通过 DMA 写回客户机 RAM。');
    add('receiver', '建立目的端恢复会话', '目的端 STOP → RESUMING', '目的端取得可写的 data_fd，驱动准备导入设备数据。计算仍在源端执行。', {targetDevice: 'RESUMING'});
    if (precopy) {
      add('device-initial', '导出 VF 上下文和初始数据', '源端 RUNNING → PRE_COPY；读取保存 FD', 'GIM 建立保存会话。目的端通过写入 FD 装入初始设备数据，源 GPU 继续计算。',
        {sourceDevice: 'PRE_COPY', initialLoaded: true, transferDevice: true, targetState: '初始数据已加载'});
      add('initial-ack', '目的端确认初始加载', 'switchover-ack：初始数据已加载', '确认的是初始设备数据。最后的显存版本和动态硬件状态要等源 VF 停止后才能确定。',
        {ack: true, transferDevice: false});
      add('vram-baseline', '传输示例显存页 F、G', 'VF 显存 → 源端环形缓冲区 → 设备 FD', '驱动复制显存，目的端写回 F、G 的第一版。两页都通过 VFIO 设备数据流传输。',
        {targetVram: [1, 1], transferDevice: true});
    }
    add('ram-baseline', '发送第一轮客户机 RAM', 'RAM 迁移 → 页面 A、B、C、D', '目的端取得四个客户机页面的第一版。VF 显存单独显示在下方。',
      {targetRam: [1, 1, 1, 1], transferRam: true, transferDevice: false});
    add('writes', 'CPU 和 GPU 继续写入', 'CPU 写 A；GPU 写显存 F，并通过 DMA 写 B', 'CPU 更新命令页 A，GPU 写显存 F，并把一份结果写回 RAM 页 B。RAM 脏页记录 A、B，显存位图记录 F。',
      {sourceRam: [2, 2, 1, 1], sourceVram: [2, 1], transferRam: false, sourceState: '计算进行中 · 显存已更新'});
    add('ram-iterate', '重传客户机 RAM 页 A、B', '合并 CPU 与 DMA 脏页，重传 A、B', '目的端 RAM 暂时追平，显存 F 的新版本仍在源端。',
      {targetRam: [2, 2, 1, 1], transferRam: true});
    if (precopy) {
      add('vram-iterate', '重传变更的显存页 F', 'GIM 查询显存位图；设备 FD 传送 F → v2', 'G 保留第一轮的副本，本轮只传 F。两端显存版本暂时相同，源 GPU 继续接受任务。',
        {targetVram: [2, 1], transferRam: false, transferDevice: true, targetState: '静态数据已导入 · 等待最终硬件状态'});
    }
    if (mode === 'cancel') {
      add('cancel-reset', '复位目的端恢复会话', 'VFIO_DEVICE_RESET：RESUMING → RUNNING', '源端仍在运行。目的 VF 复位到初始状态，客户机队列尚未启用，目的端 vCPU 保持停止。',
        {targetDevice: 'RUNNING', targetState: '已复位 · 恢复会话结束', transferRam: false, transferDevice: false, initialLoaded: false, ack: false, cancelled: true});
      add('cancel-target', '清理目的端副本', 'SET_STATE(STOP)，清理恢复资源', '目的 VF 停止，丢弃目的端 RAM 和显存副本。',
        {targetDevice: 'STOP', targetState: '已停止 · 恢复数据已丢弃', targetRam: [0, 0, 0, 0], targetVram: [0, 0]});
      add('cancel-source', '源端结束保存会话', '源端 PRE_COPY → RUNNING', '源 VF 退出预拷贝，继续使用源 GPU 和本地显存。',
        {sourceDevice: 'RUNNING', done: true});
      return frames;
    }
    add('pause-cpu', '暂停源端 vCPU', 'GPU 完成在途任务：显存 F → v3，RAM 页 C → v2', 'vCPU 已暂停。此前提交的 GPU 任务把最后一份结果写入 F，并通过 DMA 把完成记录写回 C。',
      {sourceCpu: false, sourceRam: [2, 2, 2, 1], sourceVram: [3, 1], transferRam: false, transferDevice: false, sourceState: '最后一个任务已完成'});
    add('quiesce-device', '完成设备停止转换', precopy ? '源端 PRE_COPY → STOP_COPY' : '源端 RUNNING → STOP', '驱动暂停 VF。此时显存和硬件状态固定，RAM 中也已包含最后一条完成记录。',
      {sourceDevice: precopy ? 'STOP_COPY' : 'STOP', sourceDma: false, stopped: true, sourceState: '已冻结 · 与最终 RAM 对齐'});
    if (!precopy) {
      add('begin-stopcopy', '开始导出完整设备状态', '源端 STOP → STOP_COPY', '这个对照场景把 F、G 和设备状态全部留给停机阶段。RAM 的前几轮复制已经完成。',
        {sourceDevice: 'STOP_COPY'});
    }
    add('final-ram', '同步最后一笔 DMA 写入', 'RAM 迁移传送完成页 C → v2', '目的端收到了最后一条完成记录。对应的计算结果位于显存 F，由设备数据流交付。',
      {targetRam: [2, 2, 2, 1], ramFinal: true, transferRam: true});
    add('final-device', '加载显存和最终硬件状态', precopy ? '设备 FD：F → v3，随后完成设备状态交付' : '设备 FD：传送 F、G 和完整设备状态', precopy ? 'G 已在预拷贝时到达。目的端驱动补齐 F 和动态硬件状态，验证设备数据。' : 'F、G 都在停机期间写入目的 GPU，目的端同时导入上下文和硬件状态。',
      {deviceFinal: true, targetVram: [...state.sourceVram], transferRam: false, transferDevice: true, targetState: '完整设备数据已写入'});
    add('validate', '确认完整恢复点', '结束 RESUMING，完成状态安装与校验', '目的端驱动完成导入，显存结果与 RAM 中的完成记录一致。设备配置和 vCPU 状态也已就绪。',
      {validated: true, targetDevice: 'STOP', targetState: '已校验 · 等待运行交接', transferDevice: false});
    add('handoff', '目的端接管执行', '目的端设备 RUNNING，随后恢复 vCPU', '客户机在目的 GPU 上继续计算，新的 DMA 写入目的端内存。源端保持停止。',
      {targetCpu: true, targetDma: true, targetDevice: 'RUNNING', authority: 'target', targetState: '目的端当前状态 · 开始推进'});
    add('cleanup', '清理源端保存会话', '源端 STOP_COPY → STOP', '源端状态会话结束并保持停止，后续由管理面回收源虚拟机资源。演示在完整交接处结束。',
      {sourceDevice: 'STOP', done: true});
    return frames;
  }
  function frame(mode, index) {
    const frames = buildRun(mode);
    if (!Number.isInteger(index) || index < 0 || index >= frames.length) throw new RangeError('invalid migration step');
    return {...frames[index], total: frames.length};
  }
  function step(state) { return frame(state.mode, Math.min(state.index + 1, state.total - 1)); }
  function back(state) { return frame(state.mode, Math.max(0, state.index - 1)); }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-vfio-migration-${name}]`);
    const scenario = get('scenario');
    let state = frame(scenario.value, 0);
    function paint() {
      get('status').textContent = `第 ${state.index + 1} / ${state.total} 步 · ${state.title}`;
      get('title').textContent = state.title;
      get('action').textContent = state.action;
      get('detail').textContent = state.detail;
      get('authority').textContent = state.authority === 'target' ? '运行权：目的端；源端已停止' : state.sourceCpu ? '运行权：源端' : '停机交接：两端 vCPU 均已暂停';
      get('source-cpu').textContent = state.sourceCpu ? 'vCPU 运行中' : 'vCPU 已暂停';
      get('target-cpu').textContent = state.targetCpu ? 'vCPU 运行中' : 'vCPU 未运行';
      get('source-device').textContent = state.sourceDevice;
      get('target-device').textContent = state.targetDevice;
      get('source-dma').textContent = state.sourceDma ? 'DMA 可继续写入 RAM' : 'DMA 已停止';
      get('target-dma').textContent = state.targetDma ? 'DMA 访问目的端 RAM' : '无客户机 DMA';
      get('source-arrow').textContent = state.sourceDma ? '↓ DMA' : '— DMA 停止';
      get('target-arrow').textContent = state.targetDma ? '↓ DMA' : '— DMA 停止';
      get('source-state').textContent = state.sourceState;
      get('target-state').textContent = state.targetState;
      ['source', 'target'].forEach(side => {
        get(side).dataset.running = String(state[`${side}Cpu`]);
        ['Ram', 'Vram'].forEach(region => {
          get(`${side}-${region.toLowerCase()}`).querySelectorAll('[data-page]').forEach((node, i) => {
            const version = state[`${side}${region}`][i];
            const stale = side === 'target' && version !== state[`source${region}`][i];
            node.dataset.stale = String(stale);
            node.querySelector('strong').textContent = version ? `v${version}` : '未接收';
            node.setAttribute('aria-label', `${node.querySelector('small').textContent}：${version ? `版本 ${version}` : '未接收'}${stale && version ? '，落后源端' : ''}`);
          });
        });
      });
      get('ram-flow').dataset.active = String(state.transferRam);
      get('device-flow').dataset.active = String(state.transferDevice);
      get('ram-progress').textContent = state.ramFinal ? '最终 RAM 已同步' : state.cancelled ? (state.targetRam.some(Boolean) ? '目的端副本待清理' : '目的端副本已丢弃') : state.targetRam.some(Boolean) ? '运行中副本 · 仍需最终同步' : '尚未传输';
      get('device-progress').textContent = state.deviceFinal ? '设备状态已传完' : state.cancelled ? '恢复会话已清理' : state.targetVram.some(Boolean) ? '已预拷贝设备数据' : state.initialLoaded ? '初始设备数据已加载' : '尚未传输';
      const pending = state.targetVram.filter((version, i) => version !== state.sourceVram[i]).length;
      get('vram-progress').textContent = state.cancelled ? '显存传输已取消' : `示例显存：${pending} 页待同步`;
      get('ack').textContent = state.cancelled ? '迁移已取消' : state.ack ? '目的端已确认初始数据加载完成' : state.mode === 'stopcopy' ? '设备数据在停机阶段传输' : '等待初始加载确认';
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
  return {MODES, buildRun, frame, step, back, mount};
});
