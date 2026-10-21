/* Logical VFIO migration barriers; rows are representative RAM pages, not a trace. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-vfio-migration-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const MODES = Object.freeze(['precopy', 'stopcopy', 'cancel']);
  const PAGE_NAMES = Object.freeze(['描述符页 A', '接收页 B', '接收页 C', '应用页 D']);

  function buildRun(mode = 'precopy') {
    if (!MODES.includes(mode)) throw new RangeError('unknown VFIO migration scenario');
    const precopy = mode !== 'stopcopy';
    const frames = [];
    let state = {mode, sourceCpu: true, targetCpu: false, sourceDma: true, targetDma: false,
      sourceDevice: 'RUNNING', targetDevice: 'STOP', sourceRam: [1, 1, 1, 1], targetRam: [0, 0, 0, 0],
      sourceState: '当前状态 · 持续更新', targetState: '尚未加载', initialLoaded: false,
      ack: false, ramFinal: false, deviceFinal: false, validated: false, authority: 'source',
      transferRam: false, transferDevice: false, stopped: false, cancelled: false, done: false};
    function add(key, title, action, detail, changes = {}) {
      state = {...state, ...changes, key, title, action, detail, index: frames.length};
      state.sourceRam = [...state.sourceRam]; state.targetRam = [...state.targetRam];
      frames.push(state);
    }
    add('running', '源端处理业务', '源端 RUNNING；目的端停止', '四个方块代表 RAM 中不同用途的页面，数字表示内容版本。网卡正在接收报文，运行权属于源端。');
    add('receiver', '建立目的端恢复会话', '目的端 STOP → RESUMING', '目的端取得可写的状态 data_fd，但设备保持停止，不能进行客户机 DMA。', {targetDevice: 'RESUMING'});
    if (precopy) {
      add('device-initial', '预拷贝设备初始状态', '源端 RUNNING → PRE_COPY；读取初始块 A', '源端仍能收包。目的端加载初始设备状态；块 A 是不透明数据的示意标签，不代表 RAM 页。',
        {sourceDevice: 'PRE_COPY', initialLoaded: true, transferDevice: true, targetState: '初始块 A 已加载 · 尚缺尾部'});
      add('initial-ack', '目的端确认初始加载', 'switchover-ack：初始数据已加载', '确认将部分资源准备移出停机窗口。此时 RAM 和最终设备状态尚未就绪，目的端仍不能运行。',
        {ack: true, transferDevice: false});
    }
    add('ram-baseline', '发送第一轮 RAM', 'RAM 迁移 → 页面 A、B、C、D', '目的端现在持有这些页面的第一版副本。源端继续执行，副本随后的新旧变化需要脏页跟踪发现。',
      {targetRam: [1, 1, 1, 1], transferRam: true});
    add('writes', 'CPU 与网卡继续修改 RAM', 'CPU：A → v2；DMA：B → v2', '客户机更新描述符页 A，网卡把新报文写入接收页 B。两种写入来自不同写入者，都要进入 RAM 脏页集合。',
      {sourceRam: [2, 2, 1, 1], transferRam: false, sourceState: '队列推进后的当前状态'});
    add('ram-iterate', '迭代传输变化的页面', '合并 CPU 与 DMA 脏页，重传 A、B', '目的端 RAM 暂时追上源端。设备内部状态仍会变化；RAM 追平不等于已具备完整恢复点。',
      {targetRam: [2, 2, 1, 1], transferRam: true});
    if (mode === 'cancel') {
      add('cancel-reset', '复位目的端恢复会话', 'VFIO_DEVICE_RESET：RESUMING → RUNNING', '本例在源端尚未停机、目的端从未执行客户机时取消。复位终止恢复会话，设备回到初始 RUNNING 状态；未启用客户机队列，不会继续已导入的 I/O。目的端 vCPU 仍不运行。',
        {targetDevice: 'RUNNING', targetState: '复位初始状态 · 未启用客户机队列', transferRam: false, initialLoaded: false, ack: false, cancelled: true});
      add('cancel-target', '停止并清理目的端', 'SET_STATE(STOP)，随后清理目的端副本', '确认复位后的设备进入 STOP，再丢弃目的端 RAM 和准备资源。复位回到初始 RUNNING 与停止设备是两个不同操作。',
        {targetDevice: 'STOP', targetState: '已复位并停止 · 恢复数据已丢弃', targetRam: [0, 0, 0, 0]});
      add('cancel-source', '源端结束保存会话', '源端 PRE_COPY → RUNNING', '假定转换成功，源端继续处理业务。本路径不涉及停机后设备错误，也不演示目的端已运行后的回退。',
        {sourceDevice: 'RUNNING', done: true});
      return frames;
    }
    add('pause-cpu', '暂停源端 vCPU', 'vCPU 停止；设备停止转换尚未完成', '此前提交给网卡的工作仍可能写 RAM。本步把停止过程拆开观察：接收页 C 被最后一笔 DMA 改成 v2，必须在最终同步中覆盖。',
      {sourceCpu: false, sourceRam: [2, 2, 2, 1], transferRam: false, sourceState: '包含最后一次 DMA 的队列状态'});
    add('quiesce-device', '完成设备停止转换', precopy ? '源端 PRE_COPY → STOP_COPY' : '源端 RUNNING → STOP', '驱动成功完成停止，设备不再发起 DMA、中断或改变内部状态。现在可以形成稳定的 RAM 与设备恢复点。',
      {sourceDevice: precopy ? 'STOP_COPY' : 'STOP', sourceDma: false, stopped: true, sourceState: '已冻结 · 与最终 RAM 对齐'});
    if (!precopy) {
      add('begin-stopcopy', '开始导出设备状态', '源端 STOP → STOP_COPY', '没有设备预拷贝，完整状态从停机后开始导出。RAM 的运行中预拷贝已经完成，两者能力分别判断。',
        {sourceDevice: 'STOP_COPY'});
    }
    add('final-ram', '完成最终 RAM 同步', '同步 DMA 脏页，发送 C → v2', '目的端收齐最终 RAM，源端设备仍保持停止。这里先展示 RAM，再展示设备尾部；实际发送可交错，恢复必须等待两者。',
      {targetRam: [2, 2, 2, 1], ramFinal: true, transferRam: true});
    add('final-device', '加载最终设备状态', precopy ? '发送设备尾部 B；读到永久 EOF' : '发送完整设备状态 A+B；读到永久 EOF', '目的端 data_fd 按顺序接收状态。数据已写完，还需要结束 RESUMING 的校验，并确认 CPU 与设备配置等其余状态就绪。',
      {deviceFinal: true, transferRam: false, transferDevice: true, targetState: '完整状态已写入 · 等待最终校验'});
    add('validate', '确认完整恢复点', '结束 RESUMING，完成状态安装与校验', '本步展示恢复转换中的逻辑检查点；QEMU 可通过组合转换进入运行态，无需在此另发一次 STOP。假定 RAM、CPU 与配置均已加载，源端仍停止。',
      {validated: true, targetDevice: 'STOP', targetState: '已校验 · 等待运行交接', transferDevice: false});
    add('handoff', '目的端接管执行', '目的端设备 RUNNING，随后恢复 vCPU', '运行交给目的端，DMA 访问目的端 RAM。源端继续保持停止；此后旧副本不能再直接启动。',
      {targetCpu: true, targetDma: true, targetDevice: 'RUNNING', authority: 'target', targetState: '目的端当前状态 · 开始推进'});
    add('cleanup', '清理源端保存会话', '源端 STOP_COPY → STOP', '源端状态会话结束并保持停止，后续由管理面回收源虚拟机资源。演示在完整交接处结束。',
      {sourceDevice: 'STOP', done: true});
    return frames;
  }
  function frame(mode, index) {
    const frames = buildRun(mode);
    if (!Number.isInteger(index) || index < 0 || index >= frames.length) throw new RangeError('invalid migration step');
    return {...frames[index], sourceRam: [...frames[index].sourceRam], targetRam: [...frames[index].targetRam], total: frames.length};
  }
  function step(state) { return frame(state.mode, Math.min(state.index + 1, state.total - 1)); }
  function back(state) { return frame(state.mode, Math.max(0, state.index - 1)); }
  function mount(lab) {
    const get = name => lab.querySelector(`[data-vfio-migration-${name}]`);
    const scenario = get('scenario');
    let state = frame(scenario.value, 0);
    function paint() {
      get('status').textContent = `第 ${state.index} / ${state.total - 1} 步 · ${state.title}`;
      get('title').textContent = state.title;
      get('action').textContent = state.action;
      get('detail').textContent = state.detail;
      get('authority').textContent = state.authority === 'target' ? '运行权：目的端；源端旧副本不可直接启动' : state.sourceCpu ? '运行权：源端；目的端不执行客户机业务' : '运行权尚未交给目的端；两端客户机均停止';
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
        get(`${side}-ram`).querySelectorAll('[data-page]').forEach((node, i) => {
          const version = state[`${side}Ram`][i];
          const stale = side === 'target' && version !== state.sourceRam[i];
          node.dataset.stale = String(stale);
          node.querySelector('strong').textContent = version ? `v${version}` : '未接收';
          node.querySelector('small').textContent = PAGE_NAMES[i];
          node.setAttribute('aria-label', `${PAGE_NAMES[i]}：${version ? `版本 ${version}` : '未接收'}${stale && version ? '，落后源端' : ''}`);
        });
      });
      get('ram-flow').dataset.active = String(state.transferRam);
      get('device-flow').dataset.active = String(state.transferDevice);
      get('ram-progress').textContent = state.ramFinal ? '最终 RAM 已同步' : state.cancelled ? '目的端副本已丢弃' : state.targetRam.some(Boolean) ? '运行中副本 · 仍需最终同步' : '尚未传输';
      get('device-progress').textContent = state.deviceFinal ? '设备状态已传完' : state.cancelled ? '恢复会话已清理' : state.initialLoaded ? '仅初始状态 · 缺最终尾部' : '尚未传输';
      get('ack').textContent = state.cancelled ? '迁移已取消，初始加载确认不再适用' : state.ack ? '初始加载已确认；不代表运行交接' : state.mode === 'stopcopy' ? '无设备预拷贝，不使用初始加载确认' : '等待初始加载确认';
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
  return {MODES, PAGE_NAMES, buildRun, frame, step, back, mount};
});
