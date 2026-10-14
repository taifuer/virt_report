/* Stable ordinary RAM, eager split, software write-protect logging, no NX mitigation. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-hugepage-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const PAGE_SIZE = 4096, HUGE_SIZE = 2097152, PAGE_COUNT = 512;
  const GPA_BASE = 0x00400000, PFN_BASE = 0x80000;
  function normalizeConfig(config = {}) {
    const hostPageSize = Number(config.hostPageSize ?? HUGE_SIZE);
    const guestPageSize = Number(config.guestPageSize ?? PAGE_SIZE);
    for (const size of [hostPageSize, guestPageSize]) {
      if (![PAGE_SIZE, HUGE_SIZE].includes(size)) throw new RangeError('page size must be 4096 or 2097152');
    }
    return {hostPageSize, guestPageSize};
  }
  function createState(config) {
    const options = normalizeConfig(config);
    return {
      config: options, tick: 0, logging: false, mode: options.hostPageSize === HUGE_SIZE ? 'huge' : 'small',
      pfns: Array.from({length: PAGE_COUNT}, (_, i) => PFN_BASE + i),
      writable: Array(PAGE_COUNT).fill(true), dirty: Array(PAGE_COUNT).fill(false),
      versions: Array(PAGE_COUNT).fill(0), writeFaults: 0, copiedBytes: 0, recordedWrites: 0,
      blocked: false, status: '初始映射已建立。请选择子页写入，或开启跟踪后比较权限。',
      log: ['宿主物理范围固定；客户机页大小独立于 EPT 叶子大小。']
    };
  }
  function pageIndex(value) {
    if (typeof value !== 'number' && (typeof value !== 'string' || !/^\d+$/.test(value))) throw new RangeError('page must be an integer from 0 to 511');
    const page = Number(value);
    if (!Number.isInteger(page) || page < 0 || page >= PAGE_COUNT) throw new RangeError('page must be an integer from 0 to 511');
    return page;
  }
  function pageInfo(state, value) {
    const page = pageIndex(value);
    return {
      page, gfn: GPA_BASE / PAGE_SIZE + page, gpa: GPA_BASE + page * PAGE_SIZE,
      pfn: state.pfns[page], hpa: state.pfns[page] * PAGE_SIZE,
      leafSize: state.mode === 'huge' ? HUGE_SIZE : PAGE_SIZE,
      writable: state.writable[page], dirty: state.dirty[page], version: state.versions[page]
    };
  }
  function summary(state) {
    return {
      leafCount: state.mode === 'huge' ? 1 : PAGE_COUNT,
      lowerTablePages: state.mode === 'huge' ? 0 : 1,
      dirtyCount: state.dirty.filter(Boolean).length,
      writableCount: state.writable.filter(Boolean).length,
      physicalPages: state.pfns.length
    };
  }
  function record(state, message, blocked = false) {
    state.status = message; state.blocked = blocked;
    state.log.push(`第 ${state.tick} 步 · ${message}`); state.log = state.log.slice(-4);
    return state;
  }
  function apply(previous, action) {
    if (!action || !['start', 'write', 'stop', 'recover'].includes(action.kind)) throw new RangeError('unknown action');
    const page = pageIndex(action.page ?? 3);
    const state = JSON.parse(JSON.stringify(previous)); state.tick++;
    if (action.kind === 'start') {
      if (state.logging) return record(state, '当前已启用逐页跟踪；无需再次开启。', true);
      const split = state.mode === 'huge';
      state.mode = 'small'; state.logging = true;
      state.writable.fill(false); state.dirty.fill(false); state.recordedWrites = 0;
      return record(state, `${split ? '2 MiB 叶子拆为 512 个 4 KiB 叶子，再' : '沿用 512 个 4 KiB 叶子，'}设置 W=0 并完成 TLB 刷新；跟踪已启用，PFN 全部不变。`);
    }
    if (action.kind === 'write') {
      const fault = !state.writable[page];
      if (fault) {
        state.writeFaults++;
        if (state.mode === 'huge') state.writable.fill(true);
        else state.writable[page] = true;
      }
      if (state.logging) {
        if (state.mode !== 'small') throw new Error('dirty logging requires small writable mappings');
        if (!state.dirty[page]) state.recordedWrites++;
        state.dirty[page] = true;
      }
      state.versions[page]++;
      const permission = fault ? `发生写保护缺页，恢复${state.mode === 'huge' ? '整个 2 MiB 叶子' : `P${page} 叶子`}的 W=1` : '已有写权限，无新增写保护缺页';
      return record(state, `写 P${page}：${permission}；${state.logging ? '逐页日志已记录本页' : '跟踪未开启，无新增日志'}，内容写入次数为 ${state.versions[page]}。`);
    }
    if (action.kind === 'stop') {
      if (!state.logging) return record(state, '跟踪尚未开启；当前无需关闭。', true);
      state.logging = false;
    } else if (state.logging) {
      return record(state, '逐页跟踪仍开启，不能恢复 2 MiB 叶子；先关闭跟踪。', true);
    }
    if (state.config.hostPageSize !== HUGE_SIZE) {
      return record(state, '跟踪已关闭，但宿主页表仍使用 4 KiB 映射；即使 PFN 连续，本路径也保留 512 个小页叶子。');
    }
    if (state.mode === 'huge') return record(state, '当前已是 2 MiB 叶子，无需恢复。');
    // The pinned TDP recovery path selects the first existing leaf; here all are present.
    const writable = state.writable[0];
    state.mode = 'huge'; state.writable.fill(writable);
    return record(state, `跟踪已关闭，后备条件满足：原位恢复 2 MiB 叶子并刷新 TLB。继承 P0 的 W=${writable ? '1' : '0'}${writable ? '，可直接写入' : '；后续写缺页可修复权限'}。PFN 与页内容保持不变。`);
  }
  const example = [{kind: 'start'}, {kind: 'write', page: 3}, {kind: 'write', page: 129}, {kind: 'stop'}, {kind: 'write', page: 3}];
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-hugepage-${name}]`);
    const host = get('host'), guest = get('guest'), page = get('page');
    let state = createState({hostPageSize: host.value, guestPageSize: guest.value}), demoIndex = null, selected = 3;
    const hex = value => `0x${value.toString(16).padStart(8, '0')}`;
    const make = (tag, className, value) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = value;
      return node;
    };
    function paint() {
      const info = pageInfo(state, selected), stats = summary(state);
      lab.dataset.blocked = String(state.blocked);
      get('status').textContent = state.status;
      const top = get('summary'); top.replaceChildren();
      [
        ['客户机页', state.config.guestPageSize === PAGE_SIZE ? '4 KiB' : '2 MiB'],
        ['宿主映射', state.config.hostPageSize === PAGE_SIZE ? '4 KiB' : '2 MiB'],
        ['EPT 叶子', state.mode === 'huge' ? '1 × 2 MiB' : '512 × 4 KiB'],
        ['跟踪', state.logging ? '已开启' : '已关闭']
      ].forEach(([label, value]) => { const item = make('div', ''); item.append(make('small', '', label), make('strong', '', value)); top.append(item); });
      get('map-note').textContent = state.mode === 'huge' ? '第 2 级直接终止遍历，整个范围共用一个叶子及其 W 权限。' : '第 2 级指向一个 4 KiB 页表页，第 1 级的 512 个叶子可独立设置 W。';
      const map = get('map'); map.replaceChildren(); map.dataset.mode = state.mode;
      if (state.mode === 'huge') {
        const cell = make('div', 'hugepage-large');
        cell.append(make('strong', '', `2 MiB 叶子 · W=${info.writable ? '1' : '0'}`), make('span', '', 'GPA 0x00400000 → HPA 0x80000000'), make('small', '', `所选 P${selected} 位于同一大页内`)); map.append(cell);
      } else {
        for (let index = 0; index < PAGE_COUNT; index++) {
          const cell = make('span', 'hugepage-cell', state.dirty[index] ? 'D' : state.writable[index] ? '●' : '○');
          cell.dataset.dirty = String(state.dirty[index]); cell.dataset.selected = String(index === selected);
          cell.title = `P${index} · W=${state.writable[index] ? 1 : 0} · dirty=${state.dirty[index] ? 1 : 0}`; map.append(cell);
        }
      }
      const details = get('detail'); details.replaceChildren();
      [
        ['选择', `P${selected} / GFN 0x${info.gfn.toString(16)}`], ['GPA → HPA', `${hex(info.gpa)} → ${hex(info.hpa)}`],
        ['EPT 权限', `R=1 · W=${info.writable ? 1 : 0} · X=1`],
        [state.logging ? '本周期 dirty 日志' : '上一周期 dirty 日志', `${info.dirty ? 1 : 0}${state.logging ? '' : '（跟踪停用）'}`],
        ['子页内容', `客户机累计写入 ${info.version} 次`], ['覆盖它的叶子', info.leafSize === HUGE_SIZE ? '2 MiB / 第 2 级' : '4 KiB / 第 1 级']
      ].forEach(([label, value]) => details.append(make('dt', '', label), make('dd', '', value)));
      get('backing').textContent = `所选 PFN 0x${info.pfn.toString(16)} · 映射操作复制客户机数据 ${state.copiedBytes} B`;
      get('counters').textContent = `叶子 ${stats.leafCount} 项 · 本范围的第 1 级页表 ${stats.lowerTablePages} 页 · 日志标记 ${stats.dirtyCount} 页${state.logging ? '' : '（历史）'} · 累计写保护缺页 ${state.writeFaults} 次`;
      const events = get('events'); events.replaceChildren(); state.log.forEach(line => events.append(make('li', '', line)));
      get('demo').textContent = demoIndex === null ? '示例下一步' : demoIndex === example.length ? '重新演示' : `示例下一步 · ${demoIndex}/${example.length}`;
    }
    function reset() {
      state = createState({hostPageSize: host.value, guestPageSize: guest.value});
      selected = 3; page.value = '3'; page.setCustomValidity(''); demoIndex = null; paint();
    }
    function selectPage() {
      try { selected = pageIndex(page.value); page.setCustomValidity(''); return true; }
      catch (_) { page.setCustomValidity('请输入 0 到 511 之间的整数。'); page.reportValidity(); return false; }
    }
    function dispatch(kind) { if (!selectPage()) return; demoIndex = null; state = apply(state, {kind, page: selected}); paint(); }
    ['start', 'write', 'stop'].forEach(kind => get(kind).addEventListener('click', () => dispatch(kind)));
    page.addEventListener('change', () => { if (selectPage()) paint(); });
    host.addEventListener('change', reset); guest.addEventListener('change', reset); get('reset').addEventListener('click', reset);
    get('demo').addEventListener('click', () => {
      if (demoIndex === null || demoIndex >= example.length) { state = createState({hostPageSize: host.value, guestPageSize: guest.value}); demoIndex = 0; }
      const action = example[demoIndex++];
      if (action.page !== undefined) { selected = action.page; page.value = String(selected); page.setCustomValidity(''); }
      state = apply(state, action); paint();
    });
    get('controls').hidden = false; get('scene').hidden = false; get('status').hidden = false; get('fallback').hidden = true;
    paint();
    return {state: () => state, dispatch, reset};
  }
  return {PAGE_SIZE, HUGE_SIZE, PAGE_COUNT, GPA_BASE, PFN_BASE, normalizeConfig, createState, pageIndex, pageInfo, summary, apply, example, mount};
});
