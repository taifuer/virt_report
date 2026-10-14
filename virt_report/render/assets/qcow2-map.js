/* Single-request qcow2 model: ordinary L2 entries, no real I/O or disk mutations. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-qcow2-map-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const CLUSTER = 65536, REQUEST = 4096, LIMIT = 1073741824;
  const L2_ENTRIES = CLUSTER / 8, REF_ENTRIES = CLUSTER / 2;
  const DATA = 0x90000, NEW_L2 = 0x100000, NEW_DATA = 0x110000;
  const SCENARIOS = ['shared', 'both', 'private', 'unallocated', 'zero'];
  function hex(value) { return '0x' + value.toString(16); }
  function parseAddress(value) {
    const text = String(value).trim();
    if (!/^(0x[0-9a-f]+|[0-9]+)$/i.test(text)) throw new RangeError('请输入完整的十六进制或十进制地址。');
    const address = Number(text);
    if (!Number.isSafeInteger(address) || address < 0 || address > LIMIT - REQUEST) throw new RangeError('地址需在 0—0x3ffff000 之间。');
    if (address % REQUEST) throw new RangeError('本例固定 4 KiB 对齐请求，请使用 0x1000 的整数倍。');
    return address;
  }
  function splitAddress(value) {
    const address = parseAddress(value), cluster = Math.floor(address / CLUSTER);
    return {address, cluster, l1: Math.floor(cluster / L2_ENTRIES), l2: cluster % L2_ENTRIES, within: address % CLUSTER};
  }
  function refcountIndex(offset) {
    if (!Number.isSafeInteger(offset) || offset < 0) throw new RangeError('invalid host offset');
    const cluster = Math.floor(offset / CLUSTER);
    return {cluster, table: Math.floor(cluster / REF_ENTRIES), block: cluster % REF_ENTRIES};
  }
  function normalize(config = {}) {
    const scenario = config.scenario ?? 'shared', backing = config.backing ?? true;
    if (!SCENARIOS.includes(scenario) || typeof backing !== 'boolean') throw new RangeError('invalid initial mapping');
    return {scenario, backing};
  }
  function inspect(config = {}, address = '0x20031000', operation = 'read') {
    const options = normalize(config), mapping = splitAddress(address);
    if (!['read', 'write'].includes(operation)) throw new RangeError('invalid operation');
    const {scenario, backing} = options;
    const local = ['shared', 'both', 'private'].includes(scenario);
    const source = local ? 'local' : scenario === 'zero' || !backing ? 'zero' : 'backing';
    const sourceName = {local: '本层旧数据', backing: 'backing 数据', zero: '逻辑零'}[source];
    const oldL2 = mapping.l1 === 0 ? 0x30000 : 0x40000;
    const before = {l2: oldL2, data: local ? DATA : null, kind: local ? 'normal' : scenario};
    const after = {...before}, events = [];
    const references = [{role: '原 L2 表', offset: oldL2, before: scenario === 'both' ? 2 : 1, after: scenario === 'both' ? 2 : 1}];
    if (local) references.push({role: '原数据簇', offset: DATA, before: scenario === 'private' ? 1 : 2, after: scenario === 'private' ? 1 : 2});
    const event = (kind, text) => events.push({kind, text});
    event('lookup', `客户机簇 ${mapping.cluster} → L1[${mapping.l1}] → ${hex(oldL2)} 中的 L2[${mapping.l2}]；簇内偏移 ${hex(mapping.within)}。`);
    const dataCow = operation === 'write' && local && scenario !== 'private';
    const metadataCow = operation === 'write' && scenario === 'both';
    const allocated = operation === 'write' && scenario !== 'private';
    let resultOffset = local ? DATA + mapping.within : source === 'backing' ? mapping.address : null;
    if (operation === 'read') {
      if (local) event('read-local', `普通数据簇 → 读取本文件 ${hex(resultOffset)} 的 4 KiB；共享计数不改变读路径。`);
      else if (source === 'backing') event('read-backing', `当前表项未分配 → backing 节点读取客户机偏移 ${hex(mapping.address)}，不使用本层宿主偏移。`);
      else event('read-zero', scenario === 'zero' ? '显式零映射 → 返回 4 KiB 零；即使存在 backing，也不向下读取。' : '当前表项未分配且无 backing → 返回 4 KiB 零。');
      event('unchanged', '读取不分配簇，不改变映射或引用计数。');
    } else {
      if (metadataCow) {
        event('metadata-allocate', `L2 表 refcount=2：分配 ${hex(NEW_L2)}，新表计数 0→1；先刷出分配计数。`);
        event('metadata-copy', '复制旧 L2 的表项并刷出新表；此时各数据指针仍保持原值。');
        after.l2 = NEW_L2;
        event('metadata-link', `把 L1[${mapping.l1}] 改为指向 ${hex(NEW_L2)}。`);
        references[0].after = 1;
        references.push({role: '新 L2 表', offset: NEW_L2, before: 0, after: 1});
        event('metadata-release', '旧 L2 表 refcount 2→1，其他引用继续使用旧表。');
      } else event('metadata-keep', 'L2 表 refcount=1，保留当前表的位置。');
      if (!allocated) {
        event('write-in-place', `普通数据簇 refcount=1：原位写入 ${hex(DATA + mapping.within)} 的 4 KiB，其余 60 KiB 不搬动；无需改变 L2。`);
      } else {
        event('data-allocate', `${local ? '原数据簇共享' : scenario === 'zero' ? '原映射为显式零' : '本层尚无数据簇'}：分配数据簇 ${hex(NEW_DATA)}，新计数 0→1。`);
        references.push({role: '新数据簇', offset: NEW_DATA, before: 0, after: 1});
        event('data-prepare', `新簇内 ${hex(mapping.within)}—${hex(mapping.within + REQUEST - 1)} 放入客户机数据；首部 ${mapping.within / 1024} KiB、尾部 ${(CLUSTER - mapping.within - REQUEST) / 1024} KiB 保留${sourceName}。读写可合并，此处不模拟 I/O 次数。`);
        event('data-dependency', '数据写入与保留部分准备成功；设置 L2 对新计数及 COW 数据 flush 的写回依赖。');
        after.data = NEW_DATA; after.kind = 'normal';
        event('data-link', `在当前独占 L2 中更新第 ${mapping.l2} 项 → ${hex(NEW_DATA)}，标记新数据簇独占。`);
        if (local) {
          references.find(ref => ref.role === '原数据簇').after = 1;
          event('data-release', '旧数据簇 refcount 2→1；旧内容及另一份映射保持不变。减少计数的写回受 L2 依赖约束。');
        } else event('no-old-data', '原映射没有本文件的数据簇，无旧数据计数需要减少；backing 内容保持不变。');
      }
      resultOffset = after.data + mapping.within;
    }
    const cells = Array.from({length: CLUSTER / REQUEST}, (_, i) => operation === 'write' && i === mapping.within / REQUEST ? 'new' : source);
    return {config: options, operation, mapping, before, after, source, resultOffset, references, events, cells,
      dataCow, metadataCow, allocated, preservedBytes: allocated ? CLUSTER - REQUEST : 0};
  }
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-qcow2-map-${name}]`);
    const controls = {address: get('address'), scenario: get('scenario'), backing: get('backing'), operation: get('operation')};
    const make = (tag, text, className) => {
      const node = doc.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    let state;
    const cells = [];
    for (let i = 0; i < CLUSTER / REQUEST; i++) {
      const button = make('button', undefined, 'qcow2-map-cell'); button.type = 'button';
      button.append(make('small', hex(i * REQUEST)), make('strong', ''));
      button.addEventListener('click', () => {
        if (!state) return;
        controls.address.value = hex(state.mapping.cluster * CLUSTER + i * REQUEST); refresh();
      });
      cells.push(button); get('strip').append(button);
    }
    function paint() {
      const {mapping, before, after, operation} = state;
      const pointer = data => data === null ? state.config.scenario === 'zero' ? '显式零' : '未分配' : hex(data);
      get('indices').textContent = `客户机簇 ${mapping.cluster} · L1 index ${mapping.l1} · L2 index ${mapping.l2} · 簇内偏移 ${hex(mapping.within)}`;
      get('path').textContent = `之前：L1[${mapping.l1}] → L2 @ ${hex(before.l2)}\n      L2[${mapping.l2}] → ${pointer(before.data)}\n之后：L1[${mapping.l1}] → L2 @ ${hex(after.l2)}\n      L2[${mapping.l2}] → ${pointer(after.data)}`;
      let outcome;
      if (operation === 'read') outcome = state.source === 'zero' ? '直接返回逻辑零' : `${state.source === 'backing' ? 'backing 客户机偏移' : '本文件偏移'} ${hex(state.resultOffset)}`;
      else outcome = `${state.allocated ? state.dataCow ? '数据 COW' : '首次分配数据簇' : '原位写入'} → 本文件 ${hex(state.resultOffset)}${state.metadataCow ? '；同时 COW L2 表' : '；L2 表位置不变'}`;
      get('status').textContent = `${hex(mapping.address)} · ${operation === 'read' ? '读取' : '写入'} 4 KiB：${outcome}`;
      for (let i = 0; i < cells.length; i++) {
        const value = state.cells[i], button = cells[i], label = {local: '本', backing: '底', zero: '零', new: '新'}[value];
        button.dataset.kind = value; button.querySelector('strong').textContent = label;
        button.setAttribute('aria-pressed', String(i * REQUEST === mapping.within));
        button.setAttribute('aria-label', `簇内 ${hex(i * REQUEST)}—${hex((i + 1) * REQUEST - 1)}：${{local: '本层旧数据', backing: 'backing 数据', zero: '逻辑零', new: '本次写入'}[value]}；点击选择此区间`);
      }
      get('refcounts').replaceChildren(...state.references.map(ref => {
        const row = make('tr'); row.dataset.changed = String(ref.before !== ref.after);
        row.append(make('td', `${ref.role} @ ${hex(ref.offset)}`), make('td', String(ref.before)), make('td', String(ref.after)));
        return row;
      }));
      const refOffset = before.data ?? before.l2, ref = refcountIndex(refOffset);
      get('ref-index').textContent = `计数索引示例：${hex(refOffset)} / 64 KiB = 宿主簇 ${ref.cluster} → refcount table[${ref.table}] 所指的 block[${ref.block}]。每 block 有 32768 个计数。`;
      get('trace').replaceChildren(...state.events.map(item => make('li', item.text)));
    }
    function refresh() {
      try {
        state = inspect({scenario: controls.scenario.value, backing: controls.backing.value === 'on'}, controls.address.value, controls.operation.value);
      } catch (error) {
        state = null; get('error').textContent = error.message; get('error').hidden = false;
        controls.address.setAttribute('aria-invalid', 'true'); get('scene').hidden = true; return;
      }
      controls.address.removeAttribute('aria-invalid'); get('error').textContent = ''; get('error').hidden = true;
      get('scene').hidden = false; paint();
    }
    get('query').addEventListener('submit', event => { event.preventDefault(); refresh(); });
    for (const key of ['scenario', 'backing', 'operation']) controls[key].addEventListener('change', refresh);
    get('reset').addEventListener('click', () => {
      controls.address.value = '0x20031000'; controls.scenario.value = 'shared';
      controls.backing.value = 'on'; controls.operation.value = 'read'; refresh();
    });
    refresh(); get('query').hidden = false; get('fallback').hidden = true;
    return {state: () => state, refresh};
  }
  return {CLUSTER, REQUEST, LIMIT, L2_ENTRIES, REF_ENTRIES, SCENARIOS, hex, parseAddress, splitAddress, refcountIndex, normalize, inspect, mount};
});
