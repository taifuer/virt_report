/* One L2 OUT instruction: nested reflection, kernel PIO, or outer userspace. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-nested-vmx-lab]').forEach(lab => api.mount(lab));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const MODES = Object.freeze(['l1-intercept', 'l0-eventfd', 'l0-userspace']);
  const INITIAL_RIP = 0x1000;
  const NEXT_RIP = 0x1002;

  function buildRun(mode = 'l1-intercept') {
    if (!MODES.includes(mode)) throw new RangeError('unknown nested VMX scenario');
    const frames = [];
    let state = {mode, active: 'l1', currentVmcs: '01', prepared02: false,
      intercept: mode === 'l1-intercept', rip12: INITIAL_RIP, rip02: null, exit12: false,
      reflections: 0, signals: 0, userExits: 0, ioExited: false, completed: false, done: false};
    function add(key, title, command, detail, transfer, changes = {}) {
      state = {...state, ...changes, key, title, command, detail, transfer, index: frames.length};
      frames.push(state);
    }
    add('ready', 'L1 准备进入 L2', 'VMCS12.guest_rip = 0x1000', 'L1 已准备 L2 的执行状态，并设置本次端口的 I/O bitmap 位。CPU 当前使用 VMCS01 运行 L1。', '当前位置：L1');
    add('launch', 'VMLAUNCH 进入 L0', 'L1 VMLAUNCH → nested_vmx_run()', '真实 CPU 退出到 L0。KVM 检查虚拟 VMX 权限、启动状态、控制字段及进入条件。', 'L1 → L0 · VMLAUNCH', {active: 'l0'});
    add('compose', 'L0 合成 VMCS02', 'prepare_vmcs02_early() / prepare_vmcs02()', 'L2 状态取自 VMCS12，真实宿主状态属于 L0。VMCS02 对端口 I/O 强制真实退出，是否转发 L1 稍后另行判断。', 'L0：VMCS12 与宿主约束 → VMCS02', {currentVmcs: '02', prepared02: true, rip02: INITIAL_RIP});
    add('run-l2', '物理 CPU 开始执行 L2', '真实 VM-entry · VMCS02', 'L2 在真实 VMX non-root 中执行，RIP 指向 OUT。L1 此时没有在同一个物理 CPU 上同时执行。', 'L0 → L2 · 真实 VM-entry', {active: 'l2'});
    add('io-exit', 'OUT 触发真实退出', 'OUT 0xe0, AL → I/O VM-Exit', '本次写入值为 0x41，长度为 1 字节。真实退出先交给 L0，OUT 尚未完成，L2 RIP 仍为 0x1000。', 'L2 → L0 · 硬件 I/O 退出', {active: 'l0', ioExited: true});
    if (mode === 'l1-intercept') {
      add('reflect', '保存向 L1 转发的退出', 'nested_vmx_exit_handled_io() → __nested_vmx_vmexit()', 'L1 bitmap 位为 1。KVM 把 L2 状态和本次 I/O 退出信息写入 VMCS12，切回 VMCS01，并准备 L1 的虚拟宿主状态。', 'L0：VMCS02 → VMCS12，准备恢复 L1', {currentVmcs: '01', exit12: true, reflections: 1});
      add('l1-handle', 'L1 运行退出处理程序', '真实 VM-entry · VMCS01 → L1', 'L1 读取 VMCS12 的退出原因、端口和指令长度。本例由 L1 的小型 Hypervisor 直接处理通知，不涉及外层 QEMU。', 'L0 → L1 · 呈现嵌套 I/O 退出', {active: 'l1'});
      add('l1-complete', 'L1 完成模拟并更新 RIP', 'VMCS12.guest_rip = 0x1000 + 2', 'L1 已接收这次通知，将 L2 的 RIP 移到下一条指令。VMCS02 尚待下一次嵌套进入时同步。', 'L1：完成端口写，更新 L2 恢复位置', {rip12: NEXT_RIP, completed: true});
      add('resume', 'VMRESUME 再次进入 L0', 'L1 VMRESUME → nested_vmx_run()', 'L1 请求恢复已启动的 L2，KVM 检查进入条件。VMRESUME 本身仍由 L0 实现虚拟语义。', 'L1 → L0 · VMRESUME', {active: 'l0'});
      add('recompose', '更新 VMCS02 的 L2 状态', 'VMCS12 → VMCS02', '把新的 L2 RIP 和必要控制同步到 VMCS02，准备真实 VM-entry。VMCS12 保留本次退出信息供 L1 查阅。', 'L0：重新准备 VMCS02', {currentVmcs: '02', rip02: NEXT_RIP});
    } else if (mode === 'l0-eventfd') {
      add('l0-route', 'L1 未要求拦截', 'I/O bitmap[0xe0] = 0 → L0 PIO 总线', 'KVM 不向 L1 合成这次退出。预注册的 PIO ioeventfd 匹配端口 0xe0、长度 1 和写入值 0x41。', 'L0：保留 VMCS02，查找 PIO 后端');
      add('l0-complete', '内核通知 eventfd 并完成指令', 'ioeventfd_write() → eventfd_signal()', '门铃写在 L0 内核完成，L2 当前 RIP 前移两字节。后端工作可异步执行；本次无需让 vCPU 的 KVM_RUN 返回外层 QEMU。VMCS12 尚未同步新的 L2 RIP。', 'L0 内核后端：通知 eventfd', {rip02: NEXT_RIP, completed: true, signals: 1});
    } else {
      add('l0-route', 'L1 放行，内核无匹配后端', 'I/O bitmap[0xe0] = 0 → KVM_EXIT_IO', 'KVM 不向 L1 合成退出。由于没有匹配的内核 PIO 处理对象，本次转交外层用户态设备模型。', 'L0 KVM → 外层 QEMU · KVM_RUN 返回', {userExits: 1, active: 'qemu'});
      add('qemu-handle', '外层 QEMU 处理端口写', '用户态设备模型处理 0x41', '外层 QEMU 接收 I/O 请求并模拟该端口。L1 的退出处理程序没有运行，VMCS12 也没有本次 I/O 退出记录。', 'L0 用户态：设备模型接收写入');
      add('qemu-return', '再次 KVM_RUN 完成 I/O', 'KVM_RUN → 完成回调 → 更新 L2 RIP', '外层 vCPU 线程重新进入 KVM，内核完成待处理指令并推进 L2 RIP。VMCS02 仍承担 L2 的运行。', '外层 QEMU → L0 KVM', {active: 'l0', rip02: NEXT_RIP, completed: true});
    }
    add('continue-l2', 'L2 从下一条指令继续', '真实 VM-entry · RIP = 0x1002', mode === 'l1-intercept' ? '本次 I/O 向 L1 转发一次，经过 L1 模拟和嵌套恢复，L2 已继续。' : '本次 I/O 没有向 L1 转发。L2 当前状态已前进，VMCS12 保留上次同步时的 RIP；下一次需要向 L1 交接时再同步。', 'L0 → L2 · 真实 VM-entry', {active: 'l2', done: true});
    return frames;
  }

  function frame(mode, index) {
    const frames = buildRun(mode);
    if (!Number.isInteger(index) || index < 0 || index >= frames.length) throw new RangeError('invalid nested VMX step');
    return {...frames[index], total: frames.length};
  }
  function step(state) { return frame(state.mode, Math.min(state.index + 1, state.total - 1)); }
  function back(state) { return frame(state.mode, Math.max(0, state.index - 1)); }
  function hex(value) { return value === null ? '尚未装入' : `0x${value.toString(16)}`; }

  function mount(lab) {
    const get = name => lab.querySelector(`[data-nested-vmx-${name}]`);
    const scenario = get('scenario');
    let state = frame(scenario.value, 0);
    function paint() {
      get('status').textContent = `第 ${state.index} / ${state.total - 1} 步 · ${state.title}`;
      get('step-title').textContent = state.title;
      get('command').textContent = state.command;
      get('explanation').textContent = state.detail;
      get('transfer').textContent = state.transfer;
      get('hardware').textContent = state.active === 'l0' ? '当前执行：L0 KVM · 真实 VMX root' : state.active === 'qemu' ? '当前执行：L0 用户态 QEMU · 不在 L1/L2 中' : `当前执行：${state.active.toUpperCase()} · 真实 VMX non-root`;
      lab.querySelectorAll('[data-nested-vmx-level]').forEach(node => { node.dataset.active = String(node.dataset.nestedVmxLevel === state.active); });
      lab.querySelectorAll('[data-nested-vmx-vmcs]').forEach(node => { node.dataset.current = String(node.dataset.nestedVmxVmcs === state.currentVmcs); });
      get('l0-state').textContent = state.active === 'l0' ? '当前正在处理' : '保有真实 VMX 控制权';
      get('l1-state').textContent = state.active === 'l1' ? state.reflections ? '当前处理 L2 退出' : '当前准备嵌套进入' : state.reflections ? '等待 L2 / L0 继续' : '等待 L2 运行结果';
      get('l2-state').textContent = state.active === 'l2' ? `当前执行 · RIP ${hex(state.rip02)}` : !state.prepared02 ? '等待首次进入' : state.ioExited && !state.completed ? 'OUT 尚未完成' : state.completed ? '等待恢复到下一条指令' : '等待真实 VM-entry';
      get('qemu-state').textContent = state.active === 'qemu' ? '当前模拟端口写' : state.userExits ? '已返回 KVM_RUN' : 'vCPU 线程在 KVM_RUN 内';
      get('vmcs01').textContent = state.currentVmcs === '01' ? '当前使用 · 运行 L1' : '保留 L1 恢复上下文';
      get('vmcs02').textContent = !state.prepared02 ? '尚未准备' : state.currentVmcs === '02' ? '当前使用 · 运行 L2' : '保留 L2 执行状态';
      get('bit').textContent = state.intercept ? '1 · 拦截 0xe0' : '0 · 放行 0xe0';
      get('rip12').textContent = hex(state.rip12);
      get('rip02').textContent = hex(state.rip02);
      get('exit12').textContent = state.exit12 ? 'I/O · 0xe0 · 指令 2 字节' : '无本次 I/O 退出';
      get('reflections').textContent = `${state.reflections} 次`;
      get('signals').textContent = `${state.signals} 次`;
      get('user-exits').textContent = `${state.userExits} 次`;
      get('next').disabled = state.done;
      get('back').disabled = state.index === 0;
    }
    function reset() { state = frame(scenario.value, 0); paint(); }
    get('next').addEventListener('click', () => { state = step(state); paint(); });
    get('back').addEventListener('click', () => { state = back(state); paint(); });
    get('reset').addEventListener('click', reset);
    scenario.addEventListener('change', reset);
    for (const name of ['controls', 'status', 'detail', 'transfer', 'hardware']) get(name).hidden = false;
    get('fallback').hidden = true;
    paint();
    return {getState: () => state};
  }
  return {MODES, INITIAL_RIP, NEXT_RIP, buildRun, frame, step, back, mount};
});
