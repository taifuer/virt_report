/* Shared evidence and typed queue views. Text is never parsed as machine state. */
(function () {
  'use strict';
  const element = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  function explanation(container, parts) {
    // One reading paragraph; preserve reviewed qualifications in every view.
    // Only exact duplicates are omitted, never inferred semantic equivalents.
    const seen = new Set();
    container.querySelectorAll('[data-step-part]').forEach(node => {
      const text = String(parts[node.dataset.stepPart] || '').trim();
      const key = text.replace(/\s+/g, ' ').replace(/[。.!！?？;；]+$/u, '');
      node.textContent = text;
      node.hidden = !text || seen.has(key);
      if (text) seen.add(key);
    });
  }
  function contexts(container, groups) {
    // Background responsibilities are not animation nodes or mandatory handoffs.
    container.classList.add('walkthrough-contexts');
    container.hidden = !groups.length;
    container.replaceChildren(...groups.map(group => {
      const section = element('section', undefined, 'walkthrough-context');
      section.dataset.component = group.component;
      section.append(element('small', group.context), element('h4', group.title),
        element('p', group.note));
      return section;
    }));
  }
  function sourceLink(source) {
    const link = element('a', undefined, 'walkthrough-source-link');
    link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const name = source.symbol || source.label;
    const location = [source.project, source.path].filter(Boolean).join(' · ');
    link.append(element('strong', name));
    // Keep exact commit/line anchors in href, not in the reading surface.
    link.title = location || source.label || name;
    link.setAttribute('aria-label', `${name}${location ? '，' + location : ''}，查看来源`);
    return link;
  }
  const relationLabels = {sequence:'先后操作',call:'调用',via:'经省略调用',return:'返回'};
  const entryLabels = {notification:'通知进入',async:'异步执行',callback:'回调进入',return:'返回原上下文'};
  function isImplementation(source) {
    if (['spec', 'specification', 'reference', 'hardware'].includes(source.kind)) return false;
    return ['function', 'assembly'].includes(source.kind) || Boolean(source.symbol && /\.(?:c|h|S|s|cc|cpp)$/.test(source.path || ''));
  }
  function sourcePaths(container, paths, sources, steps = []) {
    container.replaceChildren();
    container.append(element('p', '箭头表示关键执行顺序；跨线程通知、回调与返回路径按上下文分别说明。点击函数查看固定版本源码。'));
    if (steps.length) {
      const section = element('section', undefined, 'walkthrough-instruction-path');
      section.append(element('h4', '指令与执行顺序'));
      const list = element('ol');
      for (const step of steps) {
        const item = element('li'); item.dataset.sourceStep = step.id;
        item.dataset.stepIds = (step.stepIds || []).join(' ');
        item.append(element('small', step.context), element('strong', step.title));
        if (step.code) item.append(element('code', step.code));
        item.append(element('p', step.detail)); list.append(item);
      }
      section.append(list); container.append(section);
    }
    const references = new Set(), referenceNotes = [];
    let number = 0;
    paths.forEach(path => {
      const implementation = path.sourceIds.filter(id => isImplementation(sources[id]));
      path.sourceIds.filter(id => !isImplementation(sources[id])).forEach(id => references.add(id));
      if (!implementation.length) { referenceNotes.push(path.boundary); return; }
      const section = element('section', undefined, 'walkthrough-callpath');
      section.dataset.callpath = path.id;
      const flow = path.flow || 'sequence'; section.dataset.flow = flow;
      const projects = [...new Set(implementation.map(id => sources[id].project).filter(Boolean))]
        .filter(project => !path.context.toLowerCase().includes(project.toLowerCase()));
      const context = [...projects, path.context].join(' · ');
      const description = element('p', context, 'walkthrough-call-context');
      if (flow === 'return') description.append(element('span', '返回路径', 'walkthrough-call-mode'));
      section.append(element('h4', `${++number}. ${path.title}`), description);
      if (path.entry) {
        const entry = element('p', undefined, 'walkthrough-call-entry');
        entry.dataset.entryKind = path.entry.kind;
        entry.append(element('strong', entryLabels[path.entry.kind]), document.createTextNode(` · ${path.entry.label}`));
        section.append(entry);
      }
      const list = element('ol');
      list.setAttribute('role', 'list');
      list.setAttribute('aria-label', '关键实现顺序');
      implementation.forEach((id, index) => {
        const item = element('li'); item.dataset.sourceId = id;
        if (index) {
          const relation = path.relations?.find(edge => edge.from === implementation[index - 1] && edge.to === id)?.kind || flow;
          const arrow = element('span', undefined, 'walkthrough-call-arrow');
          arrow.dataset.relation = relation;
          arrow.setAttribute('role', 'img'); arrow.setAttribute('aria-label', relationLabels[relation]);
          const glyph = element('span', '→'); glyph.setAttribute('aria-hidden', 'true');
          arrow.append(glyph);
          // Keep reviewed semantics in accessible/hover text, not repeated edge captions.
          arrow.title = relationLabels[relation]; item.append(arrow);
        }
        item.append(sourceLink(sources[id])); list.append(item);
      });
      section.append(list, element('p', path.boundary, 'walkthrough-call-boundary'));
      container.append(section);
    });
    const supplement = element('section', undefined, 'walkthrough-source-supplement');
    supplement.dataset.sourceSupplement = ''; supplement.hidden = true; container.append(supplement);
    if (references.size) {
      const section = element('section', undefined, 'walkthrough-references');
      section.append(element('h4', '规范与接口依据'));
      const list = element('ul');
      references.forEach(id => { const item = element('li'); item.dataset.sourceId = id;
        item.append(sourceLink(sources[id])); list.append(item); });
      section.append(list);
      [...new Set(referenceNotes)].forEach(note => section.append(element('p', note, 'walkthrough-call-boundary')));
      container.append(section);
    }
  }
  function highlightSources(container, sourceIds, stepId, sources) {
    const supplement = container.querySelector('[data-source-supplement]');
    if (supplement && sources) {
      supplement.replaceChildren();
      const missing = sourceIds.filter(id => !container.querySelector(`[data-source-id="${id}"]`));
      supplement.hidden = !missing.length;
      if (missing.length) {
        supplement.append(element('h4', '本步补充来源'));
        const list = element('ul');
        missing.forEach(id => { const item = element('li'); item.dataset.sourceId = id;
          item.append(sourceLink(sources[id])); list.append(item); });
        supplement.append(list);
      }
    }
    container.querySelectorAll('[data-source-id]').forEach(node => {
      node.dataset.current = String(sourceIds.includes(node.dataset.sourceId));
    });
    container.querySelectorAll('[data-callpath]').forEach(node => {
      node.dataset.current = String(Boolean(node.querySelector('[data-current="true"]')));
    });
    container.querySelectorAll('[data-source-step]').forEach(node => {
      node.dataset.current = String(node.dataset.stepIds.split(' ').includes(stepId));
    });
  }
  function queue(container, state, meta, compact = false) {
    const address = value => typeof value === 'number' ? `0x${value.toString(16)}` : value ?? '—';
    const ownership = {prepared:'已准备，尚未交出',device:'设备可访问',completed:'设备已完成，等待回收',
      reclaimed:'描述符已回收',reusable:'驱动可复用',napi:'NAPI / 协议栈持有',socket:'已进入 socket 接收队列',
      consumed:'载荷已复制到用户缓冲区', 'data-ready':'数据已写入，尚未发布完成'};
    container.replaceChildren();
    container.className = compact ? 'walkthrough-queue walkthrough-queue--compact' : 'walkthrough-queue';
    container.dataset.typedQueue = '';
    if (compact) {
      container.dataset.queueSummary = '';
      const summary = element('dl', undefined, 'walkthrough-queue-summary');
      const chain = state.descriptors.length ? state.descriptors.map(desc => desc.index).join(' → ') : '尚未准备';
      const fields = [
        ['chain', `${meta.name} · 描述符链`, chain, ['descriptors']],
        ['avail', 'Available', `idx ${state.avail.index} · 设备消费 ${state.avail.consumer}`, ['avail']],
        ['used', 'Used', `idx ${state.used.index} · 驱动回收 ${state.used.consumer}`, ['used']],
        ['free', '空闲描述符', `${state.freeDescriptors} / ${meta.size}`, ['freeDescriptors']],
      ];
      for (const [key, label, value, changes] of fields) {
        const item = element('div'), detail = element('dd', value);
        detail.dataset.queueSummaryField = key;
        item.dataset.current = String((state.changed || []).some(change => changes.some(name => change === name || change.startsWith(name + '.'))));
        item.append(element('dt', label), detail); summary.append(item);
      }
      container.append(summary);
      return;
    }
    delete container.dataset.queueSummary;
    const header = element('header', undefined, 'walkthrough-queue-header');
    header.append(element('h4', `${meta.name} · Guest RAM`),
      element('span', `队列 ${meta.index} · ${meta.size} 项 · 空闲描述符 ${state.freeDescriptors}`));
    container.append(header);
    const table = element('table', undefined, 'walkthrough-descriptors');
    table.setAttribute('aria-label', `${meta.name} 描述符表`);
    const head = element('thead'), row = element('tr');
    ['描述符 / 缓冲区', 'addr', 'len', 'flags', 'next'].forEach(text => row.append(element('th', text)));
    head.append(row); table.append(head);
    const body = element('tbody');
    const descriptors = state.descriptors.length ? state.descriptors : (meta.descriptorIndices || []).map(index => ({index,label:'尚未填写',state:'unprepared'}));
    descriptors.forEach(desc => {
      const tr = element('tr'); tr.dataset.descriptor = String(desc.index);
      tr.dataset.state = desc.state;
      tr.dataset.current = String((state.changed || []).includes('descriptors'));
      const values = [`desc[${desc.index}] · ${desc.label}`, address(desc.addr), desc.len ?? '—',
        Array.isArray(desc.flags) ? (desc.flags.join(' | ') || '设备只读') : '—', desc.next ?? '—'];
      values.forEach((value, n) => { const td = element('td', String(value)); td.dataset.label = row.children[n].textContent; tr.append(td); });
      body.append(tr);
    });
    table.append(body); container.append(table);
    const rings = element('div', undefined, 'walkthrough-queue-rings');
    for (const name of ['avail', 'used']) {
      const ring = state[name], section = element('section'); section.dataset.queueRing = name;
      section.append(element('h5', name === 'avail' ? 'Available · 驱动发布' : 'Used · 设备完成'));
      const status = element('p', `${name}.idx = ${ring.index} · ${name === 'avail' ? '设备消费' : '驱动回收'}位置 = ${ring.consumer}`);
      status.dataset.current = String((state.changed || []).some(key => key === name || key.startsWith(name + '.')));
      section.append(status);
      const slots = element('div', undefined, 'walkthrough-queue-slots');
      for (let n = 0; n < meta.size; n++) {
        const record = ring.slots[n], slot = element('div');
        slot.dataset.filled = String(record !== null && record !== undefined);
        slot.dataset.returned = String(Boolean(record != null && ring.consumer > n));
        slot.dataset.current = String(Boolean(record != null && status.dataset.current === 'true'));
        slot.append(element('small', String(n)));
        let label = '—';
        if (record != null) label = name === 'avail' ? `头 ${typeof record === 'number' ? record : record.head}` : `id ${record.id}`;
        slot.append(element('span', label));
        if (name === 'used' && record != null) slot.append(element('small', `len ${record.len}`));
        slots.append(slot);
      }
      section.append(slots); rings.append(section);
    }
    container.append(rings);
    const buffers = element('div', undefined, 'walkthrough-queue-buffers');
    state.buffers.forEach(buffer => {
      const item = element('div'); item.dataset.buffer = buffer.id;
      item.dataset.current = String((state.changed || []).includes('buffers'));
      item.append(element('strong', buffer.label || buffer.id),
        element('span', `${address(buffer.addr)} · 容量 ${buffer.length} B · 有效 ${buffer.validBytes} B`),
        element('span', ownership[buffer.state] || buffer.state)); buffers.append(item);
    });
    container.append(buffers);
    if (state.response) {
      const response = state.response;
      const label = response.ready ? (response.kind === 'scsi'
        ? `response ${response.response} · status ${response.status} · resid ${response.residual} · sense_len ${response.senseLength}`
        : `status ${response.status}`) : '尚未回写响应';
      container.append(element('p', label, 'walkthrough-queue-response'));
    }
    if (meta.responseNote) container.append(element('p', meta.responseNote, 'walkthrough-queue-note'));
    if (!compact) container.append(element('p', 'WRITE 表示设备可以写此缓冲区，不是发送方向。填写环项与发布 idx 是不同步骤；回收后的旧环项仍可保留在内存中。', 'walkthrough-queue-note'));
  }
  function networkMemory(container, state, meta, frame) {
    // The same typed snapshot drives both this overview and the full queue view.
    const changed = (...keys) => (state.changed || []).some(value =>
      keys.some(key => value === key || value.startsWith(key + '.')));
    const address = value => `0x${value.toString(16)}`;
    const owners = {prepared: '驱动持有', device: '设备可访问', 'data-ready': '已写入，待发布完成',
      completed: '已完成，待驱动回收', reusable: '驱动可复用', napi: 'NAPI / 协议栈持有',
      socket: 'socket 接收队列持有', consumed: '载荷已复制到用户空间'};
    container.replaceChildren();
    const panels = element('div', undefined, 'network-memory-panels');
    const panel = (id, title, active) => {
      const section = element('section', undefined, 'network-memory-panel');
      section.dataset.networkMemoryPart = id; section.dataset.current = String(active);
      section.append(element('h5', title)); panels.append(section); return section;
    };
    const slots = (section, name) => {
      const ring = state[name], list = element('div', undefined, 'network-memory-slots');
      list.setAttribute('aria-label', `${name} 环槽位`);
      ring.slots.forEach((entry, index) => {
        const slot = element('span', entry == null ? '·' : String(name === 'avail' ? entry : entry.id));
        slot.dataset.filled = String(entry != null);
        slot.dataset.published = String(entry != null && index < ring.index);
        slot.dataset.returned = String(entry != null && index < ring.consumer);
        slot.title = `槽 ${index}：${entry == null ? '空' : name === 'avail' ? `链头 ${entry}` : `id ${entry.id}，len ${entry.len}`}`;
        list.append(slot);
      });
      section.append(list, element('p', `${name}.idx ${ring.index} · ${name === 'avail' ? '设备消费' : '驱动回收'} ${ring.consumer}`, 'network-memory-index'));
    };
    const desc = panel('descriptors', '描述符与 available', changed('descriptors', 'avail'));
    const chain = state.descriptors.length ? state.descriptors.map(d => d.index).join(' → ') : '尚未填写';
    desc.append(element('strong', `链 ${chain}`, 'network-memory-chain'));
    for (const d of state.descriptors) {
      desc.append(element('span', `desc[${d.index}] → ${address(d.addr)} · ${d.len} B · ${d.flags.includes('WRITE') ? '设备可写' : '设备只读'}`, 'network-memory-pointer'));
    }
    slots(desc, 'avail');
    const buffers = panel('buffers', '报文缓冲区', changed('buffers', 'ownership'));
    if (!state.buffers.length) buffers.append(element('p', meta.name === 'TX'
      ? '尚未关联 virtio-net 头与报文' : '尚未提交可写 RX 缓冲区'));
    for (const buffer of state.buffers) {
      const item = element('div', undefined, 'network-memory-buffer'); item.dataset.networkBuffer = buffer.id;
      item.dataset.state = buffer.state;
      item.append(element('strong', buffer.label));
      const meter = element('div', undefined, 'network-memory-bytes');
      meter.setAttribute('role', 'meter'); meter.setAttribute('aria-label', `${buffer.label} 有效字节`);
      meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', String(buffer.length));
      meter.setAttribute('aria-valuenow', String(buffer.validBytes));
      const valid = element('i'); valid.style.width = `${Math.min(100, 100 * buffer.validBytes / buffer.length)}%`;
      meter.append(valid);
      item.append(meter, element('span', `${buffer.validBytes} / ${buffer.length} B · ${owners[buffer.state] || buffer.state}`));
      if (meta.name === 'RX' && buffer.validBytes > 0 && meta.headerLength) {
        item.append(element('span', `${meta.headerLength} B virtio-net 头 + ${buffer.validBytes - meta.headerLength} B Ethernet 帧`));
      }
      buffers.append(item);
    }
    const done = panel('completion', 'used 与回收', changed('used', 'freeDescriptors'));
    const completion = state.used.slots.find(record => record != null);
    done.append(element('strong', completion ? `id ${completion.id} · len ${completion.len}` : '尚无完成项', 'network-memory-chain'));
    done.append(element('span', completion && !state.used.index ? '完成项已填写，idx 尚未发布' :
      state.used.consumer ? '本次完成已回收' : state.used.index ? '已发布完成，等待驱动回收' : '等待后端处理', 'network-memory-pointer'));
    slots(done, 'used');
    done.append(element('span', `空闲描述符 ${state.freeDescriptors} / ${meta.size}`, 'network-memory-pointer'));
    container.append(panels);
    const handoff = element('p', meta.name === 'TX'
      ? '应用 U0 → 复制到 Guest skb S1 → 描述符引用头与报文；TX used 表示后端已结束对此链的访问。'
      : 'RX 缓冲区 → NAPI / Guest 协议栈 → socket → 复制 UDP 载荷到应用 U1。', 'network-memory-handoff');
    handoff.dataset.current = String(frame.changed.some(key => ['guestSkb', 'userBuffer', 'socketBuffers'].includes(key)));
    container.append(handoff);
  }
  window.WalkthroughComponents = {explanation, sourceLink, sourcePaths, highlightSources, queue, contexts, networkMemory};
})();
