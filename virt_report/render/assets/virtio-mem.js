/* Aggregated virtio-mem resize model; each cell summarizes 512 MiB. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-virtio-mem-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const capacity = 8192, cellSize = 512, deviceBlock = 2, usableExtent = 256;
  function createState() {
    return {
      tick: 0, requested: 4096, usable: 4352, busy: false, pending: null,
      cells: Array.from({length: capacity / cellSize}, (_, i) => ({plugged: i < 8, online: i < 8, blocked: false})),
      status: '设备已提供 4 GiB，客户机已上线这些内存。可以先将目标调到 6 GiB，再逐步扩容。',
      log: ['初始状态：后端容量 8 GiB，目标和实际提供均为 4 GiB。']
    };
  }
  function totals(state) {
    return {
      plugged: state.cells.filter(cell => cell.plugged).length * cellSize,
      online: state.cells.filter(cell => cell.online).length * cellSize,
      blocked: state.cells.filter(cell => cell.blocked).length * cellSize
    };
  }
  function size(value) { return `${Number((value / 1024).toFixed(2))} GiB`; }
  function record(state, message) {
    state.status = message;
    state.log.push(`第 ${state.tick} 步 · ${message}`);
    state.log = state.log.slice(-4);
    return state;
  }
  function apply(previous, action) {
    if (!action || !['target', 'block', 'busy', 'step'].includes(action.kind)) throw new RangeError('unknown action');
    const state = JSON.parse(JSON.stringify(previous)); state.tick++;
    if (action.kind === 'target') {
      const value = Number(action.value);
      if (!Number.isInteger(value) || value < 0 || value > capacity || value % cellSize) throw new RangeError('target must be a multiple of 512 MiB within 8 GiB');
      state.requested = value;
      // Runtime requested-size updates grow, but do not shrink, the usable region.
      state.usable = Math.max(state.usable, value ? Math.min(capacity, value + usableExtent) : 0);
      if (state.pending?.kind === 'unplug') state.pending.configChanged = true;
      return record(state, `目标改为 ${size(value)}，配置通知已发出。实际提供仍为 ${size(totals(state).plugged)}；客户机继续处理${state.pending ? '，已完成的局部操作保留' : ''}。`);
    }
    if (action.kind === 'busy') {
      if (typeof action.value !== 'boolean') throw new TypeError('busy must be boolean');
      state.busy = action.value;
      return record(state, state.busy ? 'QEMU 暂不能处理 PLUG / UNPLUG 请求，后续请求将返回 BUSY。' : 'QEMU 可处理请求，客户机可以继续重试。');
    }
    if (action.kind === 'block') {
      const index = Number(action.index);
      if (!Number.isInteger(index) || index < 0 || index >= state.cells.length) throw new RangeError('invalid cell');
      const cell = state.cells[index];
      if (!cell.online) return record(state, `区域 ${index} 当前不在客户机分配器中，不能为它设置回收阻塞。`);
      cell.blocked = !cell.blocked;
      return record(state, cell.blocked ? `区域 ${index} 的各个回收子块均有暂不可迁移页，客户机将跳过这些子块。` : `区域 ${index} 的回收阻塞已解除，后续可再次尝试隔离。`);
    }
    if (state.pending) {
      const {kind, index, configChanged} = state.pending, cell = state.cells[index];
      state.pending = null;
      if (kind === 'online') {
        cell.online = true;
        return record(state, `客户机将区域 ${index} 加入可分配内存。PLUG 完成与 Linux online 是两个阶段；本步汇总了该区域的上线操作。`);
      }
      if (configChanged) {
        cell.online = true;
        return record(state, `配置已变化，本次未发送 UNPLUG。区域 ${index} 解除隔离并归还客户机分配器，驱动重读配置后重新决定下一轮操作。`);
      }
      if (state.busy) {
        cell.online = true;
        return record(state, `QEMU 对区域 ${index} 的 UNPLUG 返回 BUSY。驱动将已隔离页归还分配器，实际提供量不变，稍后重试。`);
      }
      cell.plugged = false;
      return record(state, `QEMU 完成区域 ${index} 的 discard 和状态更新，UNPLUG 返回 ACK；实际提供降为 ${size(totals(state).plugged)}。`);
    }
    const current = totals(state).plugged;
    if (current === state.requested) return record(state, `目标已达到：实际提供 ${size(current)}。可用地址范围仍为 ${size(state.usable)}；普通缩容不会立即收缩它。`);
    if (current < state.requested) {
      const index = state.cells.findIndex((cell, i) => !cell.plugged && (i + 1) * cellSize <= state.usable);
      if (index < 0) throw new Error('no available aggregate range');
      if (state.busy) return record(state, `区域 ${index} 的 PLUG 返回 BUSY，尚未提供新内存；等待宿主侧可处理后再试。`);
      state.cells[index].plugged = true;
      state.pending = {kind: 'online', index};
      return record(state, `区域 ${index} 的 PLUG 已获 ACK，实际提供增至 ${size(totals(state).plugged)}；客户机尚未把这部分内存交给分配器。`);
    }
    let index = -1;
    for (let i = state.cells.length - 1; i >= 0; i--) {
      if (state.cells[i].online && !state.cells[i].blocked) { index = i; break; }
    }
    if (index < 0) return record(state, `暂无法达到 ${size(state.requested)}：剩余 ${size(current)} 的各回收子块都有不可迁移页，尚未发送 UNPLUG。解除阻塞后可以重试。`);
    state.cells[index].online = false;
    state.pending = {kind: 'unplug', index};
    return record(state, `客户机迁出区域 ${index} 的可迁移页，隔离并保留这些页，暂不再分配；实际提供仍为 ${size(current)}，下一步请求 UNPLUG。`);
  }
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-virtio-mem-${name}]`);
    let state = createState(), history = [];
    const make = (tag, className, text) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    function dispatch(action) {
      history.push(state); history = history.slice(-200);
      state = apply(state, action); paint();
    }
    const cells = [];
    for (let i = 0; i < state.cells.length; i++) {
      const node = make('button', 'virtio-mem-cell'); node.type = 'button';
      node.addEventListener('click', () => dispatch({kind: 'block', index: i}));
      cells.push(node); get('grid').append(node);
    }
    function paint() {
      const amount = totals(state);
      get('target').value = String(state.requested); get('busy').checked = state.busy;
      get('requested').textContent = size(state.requested);
      get('plugged').textContent = size(amount.plugged);
      get('online').textContent = size(amount.online);
      get('usable').textContent = size(state.usable);
      get('status').textContent = state.status;
      get('back').disabled = history.length === 0;
      state.cells.forEach((cell, i) => {
        const pending = state.pending?.index === i ? state.pending.kind : null;
        const label = cell.blocked ? '回收受阻' : pending === 'unplug' ? '客户机已隔离' : cell.online ? '已交客户机' : cell.plugged ? '等待上线' : '未提供';
        const node = cells[i];
        node.dataset.state = cell.blocked ? 'blocked' : pending === 'unplug' ? 'isolated' : cell.online ? 'online' : cell.plugged ? 'plugged' : 'empty';
        node.disabled = !cell.online;
        node.setAttribute('aria-pressed', String(cell.blocked));
        node.setAttribute('aria-label', `区域 ${i}，${size(i * cellSize)} 至 ${size((i + 1) * cellSize)}，${label}${cell.online ? '；点击切换回收阻塞' : ''}`);
        node.replaceChildren(make('small', '', `区域 ${i}`), make('strong', '', label), make('small', '', '512 MiB'));
      });
      const events = get('events'); events.replaceChildren(); state.log.forEach(line => events.append(make('li', '', line)));
      get('step').textContent = state.pending?.kind === 'online' ? '下一步 · 客户机上线' : state.pending?.kind === 'unplug' ? '下一步 · 请求回收' : '处理下一步';
    }
    get('target').addEventListener('change', () => dispatch({kind: 'target', value: get('target').value}));
    get('busy').addEventListener('change', () => dispatch({kind: 'busy', value: get('busy').checked}));
    get('step').addEventListener('click', () => dispatch({kind: 'step'}));
    get('back').addEventListener('click', () => { if (history.length) { state = history.pop(); paint(); } });
    get('reset').addEventListener('click', () => { state = createState(); history = []; paint(); });
    ['controls', 'scene', 'status'].forEach(name => { get(name).hidden = false; });
    get('fallback').hidden = true;
    paint();
    return {state: () => state, dispatch};
  }
  return {capacity, cellSize, deviceBlock, usableExtent, createState, totals, size, apply, mount};
});
