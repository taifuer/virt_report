/* QEMU 10.2.0 teaching example: records travel before destination state changes. */
(function (root) {
  'use strict';
  const steps = Object.freeze([
    { id: 'header', title: '流头', packet: 'QEVM · v3', layer: 'stream', marker: null, bytes: 8 },
    { id: 'configuration', title: '配置摘要', packet: 'pc-q35-10.2', layer: 'stream', marker: 0x07 },
    { id: 'ram-start', title: 'RAM 初始化', packet: 'START · pc.ram', layer: 'section', marker: 0x01, ramEOS: true, footer: true },
    { id: 'ram-part-1', title: '首次页传输', packet: 'A1 B1 C1 0', layer: 'section', marker: 0x02, ramEOS: true, footer: true },
    { id: 'ram-part-2', title: '重传 A2', packet: 'PART · A2', layer: 'section', marker: 0x02, ramEOS: true, footer: true },
    { id: 'stop', title: '暂停源端', packet: '源端暂停', layer: 'local', marker: null },
    { id: 'switchover', title: '切换通知', packet: 'COMMAND · 11', layer: 'stream', marker: 0x08, command: 11, payloadBytes: 0, bytes: 5 },
    { id: 'ram-end', title: '最终 B2 / C2', packet: 'END · B2 C2', layer: 'section', marker: 0x03, ramEOS: true, footer: true },
    { id: 'cpu-sync', title: '同步寄存器', packet: 'KVM → QEMU', layer: 'local', marker: null },
    { id: 'cpu-full', title: 'vCPU 状态', packet: 'FULL · cpu', layer: 'section', marker: 0x04, footer: true },
    { id: 'device-full', title: '设备状态', packet: 'FULL · i8259', layer: 'section', marker: 0x04, footer: true },
    { id: 'eof', title: '状态流结束', packet: 'EOF · 00', layer: 'stream', marker: 0x00, bytes: 1 },
    { id: 'description', title: '诊断描述', packet: 'VMDESC · JSON', layer: 'stream', marker: 0x06, optional: true },
    { id: 'restore', title: '目的端运行', packet: '目的端恢复', layer: 'local', marker: null }
  ].map(Object.freeze));
  const headerBytes = Object.freeze([0x51, 0x45, 0x56, 0x4d, 0, 0, 0, 3]);
  const example = Object.freeze({ machine: 'pc-q35-10.2', ramBlock: 'pc.ram', ramBytes: 0x01000000, pageBytes: 4096, ramSection: 1, cpuSection: 2, picSection: 3, picInstance: 0x20, picVersion: 1 });
  function move(current, delta) { return Math.max(-1, Math.min(steps.length - 1, current + delta)); }
  function stateAt(index) {
    const at = move(Number.isFinite(index) ? Math.trunc(index) : -1, 0);
    return {
      source: at >= 13 ? '迁移完成' : at >= 5 ? '已暂停' : '运行中',
      destination: at >= 13 ? '运行中' : at >= 11 ? '等待恢复检查' : at >= 0 ? '接收中 · 未运行' : '等待迁入',
      sourcePages: at >= 4 ? ['A2', 'B2', 'C2', '0'] : at >= 3 ? ['A2', 'B1', 'C2', '0'] : ['A1', 'B1', 'C1', '0'],
      destinationPages: at >= 7 ? ['A2', 'B2', 'C2', '0'] : at >= 4 ? ['A2', 'B1', 'C1', '0'] : at >= 3 ? ['A1', 'B1', 'C1', '0'] : ['—', '—', '—', '—'],
      layout: at >= 2, cpu: at >= 9, device: at >= 10, eof: at >= 11,
      parsed: at < 0 ? '尚未读取流头' : at === 0 ? 'magic / version 已校验' : at === 1 ? '机器类型兼容' : at === 2 ? 'pc.ram 布局已核对' : at === 3 ? '收到 A1 B1 C1 / 零页' : at === 4 ? 'A1 已覆盖为 A2' : at < 7 ? '仍在等待最终脏页' : at < 9 ? '内存副本已对齐' : at === 9 ? 'CPU 对象已装载' : at === 10 ? '设备状态已装载' : at === 11 ? 'EOF ≠ 开始执行' : at === 12 ? '诊断描述读取完成' : '检查通过，启动 vCPU'
    };
  }
  function snapshot(index) {
    const position = move(Number.isFinite(index) ? Math.trunc(index) : -1, 0);
    return steps.map((step, i) => ({ ...step, state: i <= position ? 'read' : 'pending' }));
  }
  function mount(lab, environment) {
    const env = environment || root;
    const rows = [...lab.querySelectorAll('[data-stream-record]')];
    const panels = [...lab.querySelectorAll('[data-stream-panel]')];
    if (rows.length !== steps.length || panels.length !== steps.length || rows.some((row, i) => row.dataset.streamRecord !== steps[i].id)) return null;
    const el = name => lab.querySelector(`[data-stream-${name}]`);
    const back = el('back'), next = el('next'), play = el('play'), packet = el('packet');
    const reduced = env.matchMedia('(prefers-reduced-motion: reduce)');
    let index = -1, pending = null, timer = null, animation = null, playing = false, token = 0;
    function stopPlayback() {
      playing = false;
      if (timer !== null) env.clearTimeout(timer);
      timer = null;
      play.textContent = '播放';
      play.setAttribute('aria-pressed', 'false');
    }
    function cancelTransfer() {
      token += 1;
      if (animation) animation.cancel();
      animation = null;
      pending = null;
      packet.hidden = true;
      lab.dataset.transferring = 'false';
    }
    function stop() { stopPlayback(); cancelTransfer(); render(); }
    function render() {
      const selected = pending === null ? Math.max(0, index) : pending;
      const state = stateAt(index);
      rows.forEach((row, i) => {
        row.dataset.state = i === selected ? 'current' : i <= index ? 'read' : 'pending';
        const button = row.querySelector('[data-stream-select]');
        if (i === selected) button.setAttribute('aria-current', 'step');
        else button.removeAttribute('aria-current');
        panels[i].hidden = i !== selected;
      });
      el('source-state').textContent = state.source;
      el('destination-state').textContent = state.destination;
      el('parsed').textContent = state.parsed;
      el('source-cpu').textContent = index >= 8 ? 'RAX 0x2a · RIP 0x1000' : 'vCPU 正在执行';
      if (index >= 5 && index < 8) el('source-cpu').textContent = 'vCPU 已停止执行';
      el('destination-cpu').textContent = state.cpu ? 'RAX 0x2a · RIP 0x1000' : 'vCPU 状态未装载';
      el('destination-device').textContent = state.device ? 'PIC · IRR 01 / IMR 00 / ISR 00' : '设备状态未装载';
      ['source', 'destination'].forEach(side => {
        [...lab.querySelectorAll(`[data-stream-${side}-page]`)].forEach((page, i) => {
          page.querySelector('b').textContent = state[`${side}Pages`][i];
          page.dataset.pageState = side === 'destination' ? (state.destinationPages[i] === '—' ? 'missing' : state.destinationPages[i] !== state.sourcePages[i] ? 'stale' : 'current') : (state.sourcePages[i] !== state.destinationPages[i] ? 'dirty' : 'current');
        });
      });
      const step = steps[selected];
      el('status').textContent = pending !== null ? `${selected + 1} / ${steps.length} · ${step.title} · ${step.layer === 'local' ? '执行本地操作' : '传输中，目的端尚未应用'}` : index < 0 ? '准备就绪 · 从流头开始发送' : `${index + 1} / ${steps.length} · ${step.title} · ${step.layer === 'local' ? '本地操作完成' : '目的端已解析'}`;
      el('lane-label').textContent = pending !== null && step.layer === 'local' ? '本地操作 · 不产生流记录' : pending !== null ? '记录发送 → 接收解析' : index < 0 ? '有序迁移流 →' : '本条记录处理完成';
      back.disabled = index < 0 || pending !== null;
      next.disabled = index === steps.length - 1 || pending !== null;
      play.disabled = reduced.matches || (index === steps.length - 1 && !playing);
      el('motion-note').hidden = !reduced.matches;
    }
    function transfer(target) {
      cancelTransfer();
      pending = move(target, 0);
      if (pending < 0) { index = -1; pending = null; render(); return; }
      const currentToken = token;
      const targetIndex = pending;
      const step = steps[targetIndex];
      render();
      function complete() {
        if (currentToken !== token) return;
        animation = null;
        index = targetIndex;
        pending = null;
        packet.hidden = true;
        lab.dataset.transferring = 'false';
        render();
        if (index === steps.length - 1) stopPlayback();
        else if (playing) timer = env.setTimeout(() => { timer = null; transfer(index + 1); }, 1100);
      }
      if (step.layer === 'local' || reduced.matches || !packet.animate) { complete(); return; }
      packet.textContent = step.packet;
      packet.hidden = false;
      lab.dataset.transferring = 'true';
      animation = packet.animate([
        { left: '0%', transform: 'translateX(0)', opacity: 0 },
        { left: '12%', transform: 'translateX(-12%)', opacity: 1, offset: 0.15 },
        { left: '88%', transform: 'translateX(-88%)', opacity: 1, offset: 0.85 },
        { left: '100%', transform: 'translateX(-100%)', opacity: 0 }
      ], { duration: 1050, easing: 'linear', fill: 'none' });
      animation.onfinish = complete;
    }
    function select(target) {
      stopPlayback();
      cancelTransfer();
      const selected = move(target, 0);
      index = selected - 1;
      transfer(selected);
    }
    rows.forEach((row, i) => row.querySelector('[data-stream-select]').addEventListener('click', () => select(i)));
    back.addEventListener('click', () => { stopPlayback(); cancelTransfer(); index = move(index, -1); render(); });
    next.addEventListener('click', () => { stopPlayback(); transfer(index + 1); });
    el('reset').addEventListener('click', () => { stop(); index = -1; render(); });
    play.addEventListener('click', () => {
      if (playing) { stop(); return; }
      if (reduced.matches || pending !== null || index === steps.length - 1) return;
      playing = true;
      play.textContent = '暂停';
      play.setAttribute('aria-pressed', 'true');
      transfer(index + 1);
    });
    reduced.addEventListener('change', stop);
    if (env.document) env.document.addEventListener('visibilitychange', () => { if (env.document.hidden) stop(); });
    el('controls').hidden = false;
    el('status').hidden = false;
    el('scene').hidden = false;
    lab.dataset.initialized = 'true';
    render();
    return { select, stop, index: () => index, pending: () => pending, state: () => stateAt(index) };
  }
  const api = { steps, headerBytes, example, move, stateAt, snapshot, mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root.document) root.document.querySelectorAll('[data-stream-lab]').forEach(lab => mount(lab));
})(typeof window === 'undefined' ? globalThis : window);
