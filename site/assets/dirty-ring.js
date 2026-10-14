/* A deterministic dirty-log handoff model: shared page mappings, two vCPUs. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-dirty-ring-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  function normalizeConfig(config = {}) {
    const capacity = Number(config.capacity ?? 4);
    if (![2, 4, 8].includes(capacity)) throw new RangeError('capacity must be 2, 4 or 8');
    return {capacity};
  }
  function createState(config) {
    const options = normalizeConfig(config);
    return {
      config: options, tick: 0, source: Array(8).fill(1), destination: Array(8).fill(1),
      protected: Array(8).fill(true), pending: Array(8).fill(false), migration: Array(8).fill(false),
      records: 0, transfers: 0, blocked: false, action: 'ready',
      rings: Array.from({length: 2}, (_, cpu) => ({
        cpu, producer: 0, consumer: 0, reset: 0,
        slots: Array.from({length: options.capacity}, () => ({flag: 'free', page: null}))
      })),
      status: '第一轮复制已完成。选择 vCPU 和页面写入，或点击“示例下一步”。',
      log: ['初始状态：源端与目的端 P0—P7 均为 v1。']
    };
  }
  function hasReset(state) { return state.rings.some(ring => ring.slots.some(slot => slot.flag === 'reset')); }
  function used(ring) { return ring.producer - ring.reset; }
  function record(state, message, blocked = false) {
    state.blocked = blocked; state.status = message;
    state.log.push(`第 ${state.tick} 步 · ${message}`); state.log = state.log.slice(-4);
    return state;
  }
  function apply(previous, action) {
    const kinds = ['write', 'harvest', 'reprotect', 'publish', 'read'];
    if (!action || !kinds.includes(action.kind)) throw new RangeError('unknown action');
    const cpu = Number(action.cpu ?? 0), page = Number(action.page ?? 3);
    if (!Number.isInteger(cpu) || cpu < 0 || cpu > 1) throw new RangeError('invalid cpu');
    if (!Number.isInteger(page) || page < 0 || page > 7) throw new RangeError('invalid page');
    const state = JSON.parse(JSON.stringify(previous)); state.tick++; state.action = action.kind;
    if (action.kind === 'write') {
      const ring = state.rings[cpu];
      if (used(ring) >= state.config.capacity) {
        return record(state, `vCPU ${cpu} 等待回收环槽，尚未执行本次写入；另一 vCPU 仍可继续。`, true);
      }
      state.source[page]++;
      if (!state.protected[page]) {
        return record(state, `vCPU ${cpu} 再写 P${page}，内容为 v${state.source[page]}；本页尚未重新保护，无新增条目。`);
      }
      const index = ring.producer % state.config.capacity;
      if (ring.slots[index].flag !== 'free') throw new Error('ring overwrite');
      ring.slots[index] = {flag: 'dirty', page}; ring.producer++;
      state.protected[page] = false; state.records++;
      return record(state, `vCPU ${cpu} 写 P${page} 为 v${state.source[page]}，发布 DIRTY 条目至环位 ${index}${used(ring) === state.config.capacity ? '；可用槽已满，该 vCPU 等待回收' : ''}。`);
    }
    if (action.kind === 'harvest') {
      let count = 0;
      for (const ring of state.rings) {
        while (ring.consumer < ring.producer) {
          const slot = ring.slots[ring.consumer % state.config.capacity];
          if (slot.flag !== 'dirty') break;
          state.pending[slot.page] = true; slot.flag = 'reset'; ring.consumer++; count++;
        }
      }
      return record(state, count ? `顺序收割 ${count} 条记录，合并到 KVMSlot 暂存位图并标记 RESET；尚未重新保护。` : '当前没有待收割的 DIRTY 条目。');
    }
    if (action.kind === 'reprotect') {
      let count = 0;
      for (const ring of state.rings) {
        while (ring.reset < ring.producer) {
          const index = ring.reset % state.config.capacity, slot = ring.slots[index];
          if (slot.flag !== 'reset') break;
          state.protected[slot.page] = true;
          ring.slots[index] = {flag: 'free', page: null}; ring.reset++; count++;
        }
      }
      return record(state, count ? `KVM_RESET_DIRTY_RINGS 返回：已重置 ${count} 条记录并重新保护对应页，环槽可再次使用。` : '没有 RESET 条目；请先收割 DIRTY 记录。');
    }
    if (action.kind === 'publish') {
      if (hasReset(state)) return record(state, '暂不能发布：RESET 条目尚未完成重新保护。先执行“重置并保护”。', true);
      let count = 0;
      state.pending.forEach((value, index) => {
        if (value) { state.migration[index] = true; state.pending[index] = false; count++; }
      });
      return record(state, count ? `将 ${count} 个页标记合并到迁移待发送位图，清空对应的 KVMSlot 暂存位。` : 'KVMSlot 暂存位图为空，没有新增待发送页。');
    }
    if (!state.migration[page]) return record(state, `P${page} 尚未进入迁移待发送位图；读页未执行。`, true);
    state.migration[page] = false;
    state.destination[page] = state.source[page]; state.transfers++;
    return record(state, `读取并传送 P${page} 的 v${state.source[page]}；已有的环记录和暂存位保留，后续仍可再次发送该页。`);
  }
  const example = [
    {kind: 'write', cpu: 0, page: 3}, {kind: 'write', cpu: 0, page: 3},
    {kind: 'write', cpu: 1, page: 6}, {kind: 'harvest'}, {kind: 'publish'},
    {kind: 'reprotect'}, {kind: 'publish'}, {kind: 'read', page: 3},
    {kind: 'write', cpu: 1, page: 3}, {kind: 'read', page: 6},
    {kind: 'harvest'}, {kind: 'reprotect'}, {kind: 'publish'}, {kind: 'read', page: 3}
  ];
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-dirty-ring-${name}]`);
    const capacity = get('capacity'), cpu = get('cpu'), page = get('page');
    let state = createState({capacity: capacity.value}), demoIndex = null;
    const make = (tag, className, text) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    function paintBits(name, values, memory) {
      const container = get(name); container.replaceChildren();
      values.forEach((value, index) => {
        const cell = make('span', memory ? 'dirty-ring-page' : 'dirty-ring-bit');
        cell.append(make('small', '', `P${index}`), make('strong', '', memory ? `v${value}` : value ? '1' : '0'));
        if (memory) {
          cell.dataset.current = String(state.source[index] === state.destination[index]);
          if (name === 'source') cell.append(make('small', '', state.protected[index] ? '已保护' : '可写'));
        } else cell.dataset.set = String(value);
        container.append(cell);
      });
    }
    function paint() {
      lab.dataset.action = state.action; lab.dataset.blocked = String(state.blocked);
      get('status').textContent = state.status;
      const container = get('rings'); container.replaceChildren();
      state.rings.forEach(ring => {
        const row = make('div', 'dirty-ring-row'), heading = make('div', 'dirty-ring-ring-heading');
        const full = used(ring) >= state.config.capacity;
        heading.append(make('strong', '', `vCPU ${ring.cpu}`), make('span', '', full ? '等待回收' : '可运行'));
        row.append(heading);
        const slots = make('div', 'dirty-ring-slots');
        slots.style.setProperty('--dirty-ring-columns', String(Math.min(4, state.config.capacity)));
        ring.slots.forEach((slot, index) => {
          const cell = make('div', 'dirty-ring-slot'); cell.dataset.flag = slot.flag;
          cell.append(make('small', '', `环位 ${index}`), make('strong', '', slot.flag === 'free' ? '—' : `P${slot.page} · ${slot.flag === 'dirty' ? 'D' : 'R'}`));
          if (slot.flag !== 'free') {
            cell.append(make('small', '', 'slot = 0'), make('small', '', `offset = ${slot.page}`));
          }
          slots.append(cell);
        });
        row.append(slots, make('small', 'dirty-ring-indices', `生产 ${ring.producer} · 收割 ${ring.consumer} · 回收 ${ring.reset}（取模 ${state.config.capacity}）`));
        container.append(row);
      });
      paintBits('pending', state.pending, false); paintBits('migration', state.migration, false);
      paintBits('source', state.source, true); paintBits('destination', state.destination, true);
      get('pending-note').textContent = hasReset(state) ? '等待重置返回，尚不能发布' : '已收割的标记可安全合并';
      get('counters').textContent = `逻辑步 ${state.tick} · 累计日志 ${state.records} 条 · 后续页传输 ${state.transfers} 次 · 内容不同 ${state.source.filter((value, index) => value !== state.destination[index]).length} 页`;
      const events = get('events'); events.replaceChildren(); state.log.forEach(line => events.append(make('li', '', line)));
      get('demo').textContent = demoIndex === null ? '示例下一步' : demoIndex >= example.length ? '重新演示' : `示例下一步 · ${demoIndex}/${example.length}`;
    }
    function reset() { state = createState({capacity: capacity.value}); demoIndex = null; paint(); }
    function dispatch(kind) {
      demoIndex = null;
      state = apply(state, {kind, cpu: cpu.value, page: page.value}); paint();
    }
    ['write', 'harvest', 'reprotect', 'publish', 'read'].forEach(kind => get(kind).addEventListener('click', () => dispatch(kind)));
    get('demo').addEventListener('click', () => {
      if (demoIndex === null || demoIndex >= example.length) { state = createState({capacity: capacity.value}); demoIndex = 0; }
      const action = example[demoIndex++];
      if (action.cpu !== undefined) cpu.value = String(action.cpu);
      if (action.page !== undefined) page.value = String(action.page);
      state = apply(state, action); paint();
    });
    get('reset').addEventListener('click', reset); capacity.addEventListener('change', reset);
    get('controls').hidden = false; get('scene').hidden = false; get('status').hidden = false; get('fallback').hidden = true;
    paint();
    return {state: () => state, dispatch, reset};
  }
  return {normalizeConfig, createState, apply, hasReset, used, example, mount};
});
