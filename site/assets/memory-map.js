/* Deterministic address visibility model; no guest memory or device side effects. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-memory-map-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const LIMIT = 0x10000, GRANULE = 0x1000;
  function hex(value) { return '0x' + value.toString(16).padStart(4, '0'); }
  function normalize(config = {}) {
    const offset = Number(config.offset ?? 0x1000), priority = Number(config.priority ?? 2);
    const enabled = config.enabled ?? true;
    if (![0, 0x1000, 0x2000].includes(offset) || ![2, -1].includes(priority) || typeof enabled !== 'boolean') throw new RangeError('invalid layout');
    return {offset, priority, enabled};
  }
  function parseAddress(value) {
    const text = String(value).trim();
    if (!/^(0x[0-9a-f]+|[0-9]+)$/i.test(text)) throw new RangeError('请输入十六进制或十进制地址。');
    const address = Number(text);
    if (!Number.isSafeInteger(address) || address < 0 || address > LIMIT - 4) throw new RangeError('地址需在 0—0xfffc 之间。');
    if (address % 4) throw new RangeError('本例使用 4 字节对齐访问，请将地址调整为 4 的倍数。');
    return address;
  }
  function topology(config = {}) {
    const options = normalize(config);
    const bus = {id: 'bus', kind: 'container', size: 0x8000, children: [
      {id: 'regs-a', kind: 'mmio', start: 0x1000, size: 0x1000, priority: 0},
      {id: 'regs-b', kind: 'mmio', start: 0x3000, size: 0x1000, priority: 0}
    ]};
    return {id: 'system', kind: 'container', size: LIMIT, children: [
      {id: 'ram', kind: 'ram', start: 0, size: 0x8000, priority: 0},
      {id: 'window', kind: 'alias', start: 0x2000, size: 0x4000, priority: options.priority,
        enabled: options.enabled, offset: options.offset, target: bus}
    ]};
  }
  function resolve(tree, address) {
    const trace = [];
    function walk(node, relative) {
      if (node.enabled === false || relative < 0 || relative >= node.size) return null;
      if (node.kind === 'alias') {
        trace.push(`window：局部地址 ${hex(relative)} + alias_offset ${hex(node.offset)} → bus ${hex(relative + node.offset)}。`);
        const hit = walk(node.target, relative + node.offset);
        if (!hit) trace.push('bus 此处是空洞，window 没有可见叶子；继续查找 system 的低优先级区域。');
        return hit;
      }
      if (node.kind === 'container') {
        for (const child of [...node.children].sort((a, b) => b.priority - a.priority)) {
          if (child.enabled === false || relative < child.start || relative >= child.start + child.size) continue;
          trace.push(`${node.id} ${hex(relative)} 进入 ${child.id}，减去起点 ${hex(child.start)} → ${hex(relative - child.start)}。`);
          const hit = walk(child, relative - child.start);
          if (hit) return hit;
        }
        return null;
      }
      return {id: node.id, kind: node.kind, offset: relative};
    }
    const hit = walk(tree, address);
    return {hit, trace};
  }
  function flatten(tree) {
    // All model boundaries and alias offsets are multiples of GRANULE.
    const ranges = [];
    for (let start = 0; start < LIMIT; start += GRANULE) {
      const hit = resolve(tree, start).hit;
      if (!hit) continue;
      const last = ranges[ranges.length - 1];
      if (last && last.end === start && last.id === hit.id && last.offset + (last.end - last.start) === hit.offset) {
        last.end += GRANULE;
      } else ranges.push({start, end: start + GRANULE, id: hit.id, kind: hit.kind, offset: hit.offset});
    }
    return ranges;
  }
  function inspect(config, address, operation = 'read') {
    const options = normalize(config), value = parseAddress(address);
    if (!['read', 'write'].includes(operation)) throw new RangeError('invalid operation');
    const tree = topology(options), ranges = flatten(tree), {hit, trace} = resolve(tree, value);
    const range = ranges.find(r => value >= r.start && value < r.end) || null;
    if (hit) {
      trace.push(`FlatRange ${hex(range.start)}—${hex(range.end - 1)} → ${hit.id}；偏移 = ${hex(value)} − ${hex(range.start)} + ${hex(range.offset)} = ${hex(hit.offset)}。`);
      trace.push(hit.kind === 'ram'
        ? `QEMU 用户态访问分支：取得 ram 后端指针 + ${hex(hit.offset)}，${operation === 'read' ? '读取' : '写入并维护脏页状态'} 4 字节。`
        : `MemoryRegionOps.${operation}(opaque, ${hex(hit.offset)}, ${operation === 'write' ? 'value, ' : ''}4)，按设备定义${operation === 'read' ? '读取寄存器' : '处理写入副作用'}。`);
    } else trace.push('没有匹配的终止区域；该地址未映射，不生成普通 RAM 或 MMIO 访问。');
    return {config: options, address: value, operation, tree, ranges, hit, trace};
  }
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-memory-map-${name}]`);
    const controls = {enabled: get('enabled'), priority: get('priority'), offset: get('offset'), address: get('address'), operation: get('operation')};
    const make = (tag, text, className) => {
      const node = doc.createElement(tag); if (text !== undefined) node.textContent = text;
      if (className) node.className = className; return node;
    };
    let state;
    const cells = [];
    for (let start = 0; start < LIMIT; start += GRANULE) {
      const button = make('button', undefined, 'memory-map-cell'); button.type = 'button';
      button.append(make('small', hex(start)), make('strong', ''));
      button.addEventListener('click', () => { controls.address.value = hex(start + 4); refresh(); });
      cells.push(button); get('strip').append(button);
    }
    function paint() {
      const c = state.config;
      get('tree').textContent = `system：0x0000—0xffff\n├ ram：0x0000—0x7fff，priority = 0\n└ window：0x2000—0x5fff，${c.enabled ? '启用' : '关闭'}\n  priority = ${c.priority}，alias_offset = ${hex(c.offset)}\n  → bus：regs-a @ 0x1000，regs-b @ 0x3000`;
      get('ranges').replaceChildren();
      for (const range of state.ranges) {
        const row = doc.createElement('tr');
        row.dataset.selected = String(state.address >= range.start && state.address < range.end);
        row.append(make('td', `${hex(range.start)}—${hex(range.end - 1)}`), make('td', range.id), make('td', hex(range.offset)));
        get('ranges').append(row);
      }
      for (let start = 0; start < LIMIT; start += GRANULE) {
        const hit = resolve(state.tree, start).hit;
        const button = cells[start / GRANULE];
        const label = hit ? {ram: 'RAM', 'regs-a': 'A', 'regs-b': 'B'}[hit.id] : '空';
        button.dataset.kind = hit ? hit.kind : 'unmapped';
        button.setAttribute('aria-label', `${hex(start)}—${hex(start + GRANULE - 1)}：${hit ? hit.id : '未映射'}，查看 ${hex(start + 4)}`);
        button.setAttribute('aria-pressed', String(state.address >= start && state.address < start + GRANULE));
        button.querySelector('strong').textContent = label;
      }
      get('trace').replaceChildren(...state.trace.map(line => make('li', line)));
      get('status').textContent = state.hit
        ? `${hex(state.address)} → ${state.hit.id} + ${hex(state.hit.offset)} · ${state.operation === 'read' ? '读取' : '写入'} 4 字节`
        : `${hex(state.address)} → 未映射`;
    }
    function refresh() {
      try {
        state = inspect({enabled: controls.enabled.value === 'on', priority: controls.priority.value, offset: controls.offset.value}, controls.address.value, controls.operation.value);
      } catch (error) {
        get('error').textContent = error.message; get('error').hidden = false;
        controls.address.setAttribute('aria-invalid', 'true');
        get('scene').hidden = true;
        return;
      }
      controls.address.removeAttribute('aria-invalid'); get('error').textContent = ''; get('error').hidden = true;
      get('scene').hidden = false; paint();
    }
    get('query').addEventListener('submit', event => { event.preventDefault(); refresh(); });
    for (const key of ['enabled', 'priority', 'offset', 'operation']) controls[key].addEventListener('change', refresh);
    get('reset').addEventListener('click', () => {
      controls.enabled.value = 'on'; controls.priority.value = '2'; controls.offset.value = '4096';
      controls.address.value = '0x2004'; controls.operation.value = 'read'; refresh();
    });
    get('controls').hidden = false; get('query').hidden = false; get('fallback').hidden = true;
    refresh();
    return {state: () => state, refresh};
  }
  return {LIMIT, GRANULE, hex, normalize, parseAddress, topology, resolve, flatten, inspect, mount};
});
