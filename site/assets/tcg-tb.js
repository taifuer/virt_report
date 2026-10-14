/* QEMU 10.2 x86 TB lookup, direct links and overlapping-code invalidation.
 * A/B/C are teaching identities under a fixed code mapping and CPU context,
 * not a claim that the shared TB hash is indexed by virtual PC alone.
 * x86 system CF_PCREL permits reuse independent of an absolute virtual PC.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-tcg-tb-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const BLOCKS = Object.freeze({
    A: Object.freeze({pc: 0x1000, end: 0x101f, page: 0x1000}),
    B: Object.freeze({pc: 0x1fe0, end: 0x1fff, page: 0x1000}),
    C: Object.freeze({pc: 0x2000, end: 0x201f, page: 0x2000})
  });
  const hex = number => '0x' + number.toString(16);
  function initial() {
    return {pc: 'A', previous: null, sequence: 0, executed: 0, cache: {}, links: {}, history: [], last: null};
  }
  function copy(state) {
    return {...state, previous: state.previous ? {...state.previous} : null,
      cache: Object.fromEntries(Object.entries(state.cache).map(([key, value]) => [key, {...value}])),
      links: Object.fromEntries(Object.entries(state.links).map(([key, value]) => [key, {...value}])),
      history: state.history.slice()};
  }
  function record(state, event) {
    state.sequence += 1;
    state.last = {...event, number: state.sequence};
    state.history = [state.last, ...state.history].slice(0, 8);
    return state;
  }
  function step(old, branch = 'A') {
    if (branch !== 'A' && branch !== 'C') throw new RangeError('B 的分支方向请选择 A 或 C。');
    const state = copy(old), id = state.pc, prior = state.previous;
    const previous = prior && state.cache[prior.id];
    const validPrevious = previous && previous.valid && previous.generation === prior.generation;
    const samePage = validPrevious && BLOCKS[prior.id].page === BLOCKS[id].page;
    const edge = prior ? `${prior.id}>${id}` : null;
    const link = edge && state.links[edge];
    let tb = state.cache[id], kind, newLink = null;
    const direct = validPrevious && tb?.valid && link && link.from === prior.generation && link.to === tb.generation;
    if (direct) kind = 'direct';
    else if (tb?.valid) kind = validPrevious && !samePage ? 'helper-hit' : 'dispatch-hit';
    else {
      kind = 'translate';
      tb = state.cache[id] = {generation: (tb?.generation || 0) + 1, valid: true};
    }
    if (!direct && samePage) {
      state.links[edge] = {from: prior.generation, to: tb.generation};
      newLink = `${prior.id} → ${id}`;
    }
    const next = id === 'B' ? branch : 'B';
    const stages = kind === 'direct' ? ['已修补跳转', `执行 TB ${id}#${tb.generation}`, `PC → ${next}`]
      : kind === 'helper-hit' ? ['lookup_tb_ptr 查找命中', 'goto_ptr', `执行 TB ${id}#${tb.generation}`, `PC → ${next}`]
      : kind === 'dispatch-hit' ? ['主循环查找命中', ...(newLink ? ['修补直接链接'] : []), `执行 TB ${id}#${tb.generation}`, `PC → ${next}`]
      : [...(validPrevious && !samePage ? ['helper 查找未命中'] : []), '主循环查找未命中', '生成 TB', ...(newLink ? ['修补直接链接'] : []), `执行 TB ${id}#${tb.generation}`, `PC → ${next}`];
    const labels = {direct: '沿已建立的直接链接进入，不进行 TB 查找', 'helper-hit': '跨页分支的 helper 查找命中，使用 goto_ptr 进入', 'dispatch-hit': '主循环查找命中，无需重新翻译', translate: '查找未命中，主循环生成 TB 后进入'};
    const message = `执行 ${id}#${tb.generation}：${labels[kind]}。${newLink ? `建立 ${newLink} 链接。` : ''}下一 PC 为 ${next}（${hex(BLOCKS[next].pc)}）。`;
    state.executed += 1; state.previous = {id, generation: tb.generation}; state.pc = next;
    return record(state, {action: 'step', kind, block: id, generation: tb.generation, next, newLink, stages, message});
  }
  function invalidate(old) {
    const state = copy(old), affected = [], removed = [];
    const start = 0x1000, end = 0x1fff;
    for (const [id, tb] of Object.entries(state.cache)) {
      if (tb.valid && BLOCKS[id].pc <= end && BLOCKS[id].end >= start) {
        tb.valid = false; affected.push(`${id}#${tb.generation}`);
      }
    }
    for (const edge of Object.keys(state.links)) {
      const [from, to] = edge.split('>');
      if (!state.cache[from].valid || !state.cache[to].valid) {
        removed.push(edge.replace('>', ' → ')); delete state.links[edge];
      }
    }
    // A host-side write is an external event: resume through the execution loop.
    state.previous = null;
    const message = `改写 ${hex(start)}—${hex(end)}。${affected.length ? `${affected.join('、')} 与写入范围交叠，标记失效。` : '当前没有与该范围交叠的有效 TB。'}${removed.length ? `解除 ${removed.join('、')} 链接。` : ''}C 的代码范围未变。`;
    return record(state, {action: 'invalidate', affected, removed, stages: ['写入代码范围', '重叠 TB 失效', '解除入向与出向链接'], message});
  }
  function reenter(old) {
    const state = copy(old); state.pc = 'A'; state.previous = null;
    return record(state, {action: 'reenter', stages: ['回到主循环', 'PC 设为 A'], message: '从主循环重新进入 A。已有有效 TB 和链接保留，下次执行仍先查找 A。'});
  }
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-tcg-tb-${name}]`);
    const make = (tag, text, className) => {
      const node = doc.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    let state = initial();
    function render() {
      get('scene').hidden = false;
      get('status').textContent = state.last?.message || '当前 PC 为 A（0x1000），尚未生成任何 TB。点击“执行一步”开始。';
      get('key').textContent = `下一入口：${state.pc} · PC=${hex(BLOCKS[state.pc].pc)} · cs_base=0 · flags=F0 · cflags=C0 · 代码页映射固定`;
      get('step').textContent = `执行 ${state.pc} 一步`;
      for (const [name, ids] of [['page-one', ['A', 'B']], ['page-two', ['C']]]) {
        get(name).replaceChildren(...ids.map(id => {
          const tb = state.cache[id], node = make('div', undefined, 'tcg-tb-block');
          node.dataset.state = tb ? (tb.valid ? 'valid' : 'invalid') : 'empty';
          node.dataset.current = String(id === state.pc);
          node.append(make('strong', `${id} · ${hex(BLOCKS[id].pc)}`),
            make('span', tb ? `TB ${id}#${tb.generation} · ${tb.valid ? '有效' : '已失效'}` : '尚未翻译'),
            make('small', id === state.pc ? '下一入口' : `指令范围至 ${hex(BLOCKS[id].end)}`));
          return node;
        }));
      }
      const links = Object.entries(state.links).map(([edge, link]) => {
        const [from, to] = edge.split('>'); return make('span', `${from}#${link.from} → ${to}#${link.to}`);
      });
      get('links').replaceChildren(...(links.length ? links : [make('span', '尚无直接链接', 'tcg-tb-no-link')]));
      get('stages').replaceChildren(...(state.last?.stages || ['等待执行']).map((label, index) => make('span', `${index + 1}. ${label}`)));
      get('trace').replaceChildren(...state.history.map(event => make('li', `${event.number}. ${event.message}`)));
    }
    function act(action) {
      get('error').hidden = true; get('error').textContent = ''; get('branch').removeAttribute('aria-invalid');
      try { state = action(state); render(); }
      catch (error) { get('error').hidden = false; get('error').textContent = error.message; get('branch').setAttribute('aria-invalid', 'true'); }
    }
    get('step').addEventListener('click', () => act(current => step(current, get('branch').value)));
    get('invalidate').addEventListener('click', () => act(invalidate));
    get('reenter').addEventListener('click', () => act(reenter));
    get('reset').addEventListener('click', () => { get('branch').value = 'A'; act(initial); });
    get('branch').addEventListener('change', () => { get('error').hidden = true; get('error').textContent = ''; get('branch').removeAttribute('aria-invalid'); });
    render(); get('controls').hidden = false; get('fallback').hidden = true;
    return {state: () => state};
  }
  return {BLOCKS, initial, step, invalidate, reenter, mount};
});
