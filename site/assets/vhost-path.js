/* Logical stages of a split-ring TX request; no performance timing is inferred. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-vhost-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const BACKENDS = {
    qemu: {name: 'QEMU 用户态', control: 'QEMU 进程内设备与网络后端配置', memory: 'QEMU 通过自身的客户机内存映射访问队列和 P1。', outlet: 'TAP → 宿主网络', kick: 'KVM ioeventfd → QEMU 事件处理', call: 'QEMU virtio 通知 → KVM 中断路由'},
    kernel: {name: '内核 vhost-net', control: 'QEMU → /dev/vhost-net ioctl', memory: 'QEMU 向 vhost-net 提供内存区域信息，内核建立访问关系。', outlet: 'TAP socket → 宿主网络', kick: 'KVM ioeventfd → vhost 工作项', call: 'call eventfd → KVM irqfd → 客户机'},
    user: {name: '外部 vhost-user 后端', control: 'QEMU → Unix socket 控制消息', memory: 'QEMU 传递内存区域与共享内存 FD；后端映射同一份客户机 RAM。', outlet: '外部后端 → 网络数据面', kick: 'KVM ioeventfd → 外部后端', call: 'call eventfd → KVM irqfd → 客户机'}
  };
  const LAST_STEP = 12;
  function normalizeConfig(config = {}) {
    const backend = config.backend || 'kernel';
    if (!Object.prototype.hasOwnProperty.call(BACKENDS, backend)) throw new RangeError('unknown backend');
    return {backend};
  }
  function frame(config, index) {
    const options = normalizeConfig(config);
    if (!Number.isInteger(index) || index < 0 || index > LAST_STEP) throw new RangeError('invalid stage');
    const selected = BACKENDS[options.backend];
    const external = options.backend === 'user';
    const qemu = options.backend === 'qemu';
    const stages = [
      ['准备', '先配置后端，再提交一个 TX 请求。客户机 RAM 中尚无本次 available 项。', ''],
      ['协商设备能力', qemu ? 'QEMU 根据设备配置与客户机完成 virtio 特性协商。' : 'QEMU 查询后端能力，并把本次生效的 virtio 特性传给后端。外部协议还需协商控制特性。', 'control'],
      ['建立内存访问', selected.memory, 'control'],
      ['配置队列位置', '设置 split ring 的描述符、available 与 used 地址、大小和消费位置；客户机队列由选定后端处理。', 'control'],
      ['连接通知通道', qemu ? '客户机通知地址关联 ioeventfd；QEMU 事件处理接收 kick，设备通知按中断路由返回客户机。' : `${external ? '经 Unix socket 传递' : '经 ioctl 配置'} kick 和 call eventfd。SET_VRING_* 操作配置句柄，本阶段不发送报文。`, 'control'],
      ['后端就绪', qemu ? 'virtio-net 与 TAP 已连接，QEMU 可以处理发送队列。' : external ? '共享内存、队列和通知关系已建立；设备与队列状态允许后端开始处理。' : '队列与通知已配置，vhost-net 绑定 TAP 后端；QEMU 不再逐包消费该队列。', 'worker'],
      ['客户机发布 P1', '驱动准备网络头与 P1，填写描述符链，完成内存顺序操作后发布 available 项。缓冲区在完成前保持有效。', 'ram'],
      ['kick 提交通知', `${selected.kick}。本例需要发送 kick；实际驱动会按通知抑制条件决定是否唤醒。`, 'kick'],
      ['后端读取请求', `${selected.name}读取描述符和 P1。${external ? '访问已映射的共享 RAM；报文内容不通过控制 socket 传输。' : '报文来源是客户机 RAM，后端按描述符地址访问。'}`, 'data'],
      ['向网络出口提交', qemu ? 'QEMU 通过网络后端写 TAP，宿主网络接收 P1。' : external ? '外部后端向其网络数据面提交 P1，具体端口、交换和复制方式由实现决定。' : 'vhost-net 在内核中向 TAP 对应的 socket 提交 P1，正常数据处理不经 QEMU 用户态。', 'network'],
      ['发布 used 完成项', '本例发送处理已完成，后端不再引用客户机报文缓冲区；先写 used 项，再按内存顺序要求更新 used.idx。', 'used'],
      ['完成通知', `${selected.call}。本例满足通知条件；used 项已经可见，驱动可读取完成记录。`, 'call'],
      ['驱动回收缓冲区', '驱动读取 used 项，回收 P1 的描述符及缓冲区。本次本地发送请求结束，远端接收状态由网络协议确认。', 'ram']
    ];
    const [title, detail, active] = stages[index];
    return {
      config: options, index, title, detail, active,
      phase: index === 0 ? 'ready' : index <= 5 ? 'setup' : index === LAST_STEP ? 'done' : 'transfer',
      configured: index >= 5, published: index >= 6, kicked: index >= 7,
      consumed: index >= 8, submitted: index >= 9, completed: index >= 10,
      notified: index >= 11, reclaimed: index >= 12,
      avail: index >= 6 ? 1 : 0, used: index >= 10 ? 1 : 0,
      socketPacketBytes: 0,
      history: stages.slice(0, index + 1).map((stage, number) => ({index: number, title: stage[0]}))
    };
  }
  function createState(config) { return frame(config, 0); }
  function step(state) { return frame(state.config, Math.min(LAST_STEP, state.index + 1)); }
  function back(state) { return frame(state.config, Math.max(0, state.index - 1)); }
  function describe(state) { return `第 ${state.index} / ${LAST_STEP} 步 · ${state.title} · ${BACKENDS[state.config.backend].name}`; }
  function mount(lab, environment) {
    const doc = environment.document;
    const get = name => lab.querySelector(`[data-vhost-${name}]`);
    const selector = get('backend'), play = get('play'), next = get('next'), previous = get('back');
    let state = createState({backend: selector.value}), timer = null, playing = false, generation = 0;
    function paint() {
      const selected = BACKENDS[state.config.backend];
      lab.dataset.phase = state.phase; lab.dataset.backend = state.config.backend;
      lab.dataset.playing = String(playing);
      get('status').textContent = describe(state);
      get('step-title').textContent = state.title; get('step-detail').textContent = state.detail;
      get('control-path').textContent = selected.control;
      get('control-detail').textContent = state.config.backend === 'user' ? '控制消息与 FD 传递；本例报文走共享内存' : state.config.backend === 'kernel' ? '内存、队列、通知及 TAP 后端配置' : '设备状态、队列配置与 TAP 连接';
      get('avail').textContent = String(state.avail); get('used').textContent = String(state.used);
      get('payload').textContent = state.reclaimed ? 'P1 缓冲区可复用' : state.published ? 'P1 保持有效，等待回收' : '等待准备 P1';
      get('desc').textContent = state.published ? 'desc[0] → 网络头与 P1' : '描述符链尚未发布';
      get('buffer').textContent = state.reclaimed ? '驱动已回收' : state.completed ? '已完成，等待驱动回收' : state.published ? '驱动不可提前复用' : '客户机持有缓冲区';
      get('kick').textContent = selected.kick; get('call').textContent = selected.call;
      get('outlet').textContent = selected.outlet;
      get('network-state').textContent = state.submitted ? '本次请求已向网络数据面提交' : '等待选定后端提交 P1';
      get('delivered').hidden = !state.submitted;
      lab.querySelectorAll('[data-vhost-node]').forEach(node => { node.dataset.active = String(node.dataset.vhostNode === state.active); });
      lab.querySelectorAll('[data-vhost-edge]').forEach(node => { node.dataset.active = String(node.dataset.vhostEdge === state.active); });
      lab.querySelectorAll('[data-vhost-worker]').forEach(node => {
        const chosen = node.dataset.vhostWorker === state.config.backend;
        node.dataset.selected = String(chosen);
        node.dataset.active = String(chosen && ['worker', 'kick', 'data', 'used', 'call'].includes(state.active));
        const status = node.querySelector('[data-vhost-worker-status]');
        status.textContent = !chosen ? '本次不处理队列' : state.phase === 'ready' || state.index < 5 ? '等待配置' : state.reclaimed ? '本次请求完成' : state.completed ? '已发布 used' : state.consumed ? '已读取 P1' : '就绪，等待请求';
      });
      previous.disabled = state.index === 0;
      next.disabled = state.phase === 'done'; play.disabled = state.phase === 'done';
    }
    function pause() {
      playing = false; generation++;
      if (timer !== null) environment.clearTimeout(timer);
      timer = null; lab.dataset.playing = 'false';
      play.textContent = '播放'; play.setAttribute('aria-pressed', 'false');
    }
    function advance() {
      state = step(state);
      if (state.phase === 'done') pause();
      paint();
    }
    function schedule() {
      const token = generation;
      timer = environment.setTimeout(() => {
        if (!playing || generation !== token) return;
        advance();
        if (playing) schedule();
      }, 1700);
    }
    next.addEventListener('click', () => { pause(); advance(); });
    previous.addEventListener('click', () => { pause(); state = back(state); paint(); });
    play.addEventListener('click', () => {
      if (playing) { pause(); return; }
      if (state.phase === 'done') return;
      playing = true; generation++;
      lab.dataset.playing = 'true'; play.textContent = '暂停'; play.setAttribute('aria-pressed', 'true');
      schedule();
    });
    function reset() { pause(); state = createState({backend: selector.value}); paint(); }
    get('reset').addEventListener('click', reset);
    selector.addEventListener('change', reset);
    environment.addEventListener('pagehide', pause);
    environment.addEventListener('resize', pause);
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) pause(); });
    get('controls').hidden = false; get('status').hidden = false;
    get('detail').hidden = false; get('fallback').hidden = true;
    paint();
    return {pause, getState: () => state};
  }
  return {BACKENDS, LAST_STEP, normalizeConfig, createState, step, back, describe, mount};
});
