/* Shared event views. Playback owns the snapshots; these views never advance them. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WalkthroughEvents = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const display = value => value === undefined || value === null ? '—'
    : typeof value === 'object' ? JSON.stringify(value) : String(value);
  function changes(previous, current, labels) {
    return Object.entries(labels).filter(([key]) =>
      JSON.stringify(previous[key]) !== JSON.stringify(current[key]))
      .map(([key, label]) => ({key, label, before: display(previous[key]), after: display(current[key])}));
  }
  function groups(scene, trace) {
    if (!scene.parallel) return [{id: 'main', title: '事件顺序', frames: trace}];
    const fork = trace.findIndex(frame => frame.id === scene.parallel.after);
    return [{id: 'common', title: '共同前序', frames: trace.slice(0, fork + 1)},
      ...scene.parallel.branches.map(branch => ({id: branch.id, title: branch.title,
        summary: branch.summary, frames: trace.filter(frame => branch.steps.includes(frame.id))}))];
  }
  function lanes(scene) {
    const result = scene.layout.domains.filter(domain => domain.nodes.length).map(domain => ({id: domain.id,
      title: domain.title, nodes: domain.nodes}));
    if (scene.layout.memory) result.push({id: 'shared-memory', title: scene.layout.memory.title,
      nodes: scene.layout.memory.nodes});
    return result;
  }
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function sequence(container, scene, trace, current, select) {
    const previousEvent = container.dataset.currentEvent;
    container.dataset.currentEvent = `${scene.id || 'disk'}:${trace[current].id}`;
    const focus = container.contains(document.activeElement) ? document.activeElement.dataset.eventId : null;
    const scroll = container.scrollTop;
    const columns = lanes(scene), byNode = new Map();
    columns.forEach((lane, index) => lane.nodes.forEach(id => byNode.set(id, index)));
    const nodeNames = Object.fromEntries(scene.nodes.map(node => [node.id, node.label]));
    container.replaceChildren();
    container.classList.add('walkthrough-sequence');
    container.append(element('p', scene.parallel
      ? '分叉后各路独立推进；图中分别保留每一路的先后关系。选择事件可查看对应状态，观看顺序不代表实际调度顺序。'
      : '纵向表示本例的事件顺序，列表示执行位置或共享内存。点击事件查看状态；行距不表示耗时。', 'walkthrough-sequence-help'));
    for (const group of groups(scene, trace)) {
      const section = element('section', undefined, 'walkthrough-sequence-group');
      section.dataset.eventGroup = group.id;
      if (scene.parallel) section.append(element('h4', group.title), element('p', group.summary || '完成这些操作后进入两路独立处理。'));
      const header = element('div', undefined, 'walkthrough-sequence-row walkthrough-sequence-head');
      header.append(element('span', '事件'));
      const headings = element('div', undefined, 'walkthrough-sequence-lanes');
      headings.style.setProperty('--sequence-lanes', columns.length);
      columns.forEach(lane => headings.append(element('span', lane.title)));
      header.append(headings); section.append(header);
      for (const frame of group.frames) {
        const row = element('div', undefined, 'walkthrough-sequence-row');
        row.dataset.current = String(frame.index === current);
        row.dataset.pending = String(frame.index > current);
        const button = element('button', undefined, 'walkthrough-event-jump');
        button.type = 'button'; button.dataset.eventId = frame.id;
        if (frame.index === current) button.setAttribute('aria-current', 'step');
        button.append(element('strong', frame.title), element('small', frame.actor));
        button.addEventListener('click', () => select(frame.index)); row.append(button);
        const edges = (frame.links || []).map(id => scene.links.find(edge => edge.id === id)).filter(Boolean);
        const graphic = element('div', undefined, 'walkthrough-sequence-graphic');
        const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
        const width = 720, height = Math.max(58, edges.length * 40 + 16);
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('aria-hidden', 'true');
        const x = id => (byNode.get(id) + .5) * width / columns.length;
        for (let n = 0; n < columns.length; n++) {
          const line = document.createElementNS(ns, 'path');
          const at = (n + .5) * width / columns.length;
          line.setAttribute('d', `M ${at} 0 V ${height}`); line.setAttribute('class', 'walkthrough-lifeline'); svg.append(line);
        }
        edges.forEach((edge, n) => {
          if (!byNode.has(edge.from) || !byNode.has(edge.to)) return;
          const from = x(edge.from), to = x(edge.to), y = n * 40 + 30;
          const same = from === to, endY = same ? y + 14 : y;
          const path = document.createElementNS(ns, 'path');
          path.dataset.kind = edge.kind;
          path.setAttribute('d', same ? `M ${from} ${y} h 28 v 14 H ${to}` : `M ${from} ${y} H ${to}`);
          svg.append(path);
          const arrow = document.createElementNS(ns, 'path'), sign = same || from > to ? 1 : -1;
          arrow.dataset.kind = edge.kind;
          arrow.setAttribute('d', `M ${to + sign * 6} ${endY - 4} L ${to} ${endY} L ${to + sign * 6} ${endY + 4}`);
          svg.append(arrow);
          const label = document.createElementNS(ns, 'text');
          label.setAttribute('x', String((from + to) / 2)); label.setAttribute('y', String(y - 7));
          label.setAttribute('text-anchor', 'middle'); label.textContent = edge.label;
          svg.append(label);
        });
        if (!edges.length) for (const id of frame.active || []) {
          if (!byNode.has(id)) continue;
          const dot = document.createElementNS(ns, 'circle');
          dot.setAttribute('cx', String(x(id))); dot.setAttribute('cy', '29'); dot.setAttribute('r', '4'); svg.append(dot);
        }
        graphic.append(svg);
        const caption = element('span', edges.map(edge => `${nodeNames[edge.from]} → ${edge.label} → ${nodeNames[edge.to]}`).join('；')
          || (frame.active || []).map(id => nodeNames[id]).join('、') || '初始状态', 'walkthrough-sequence-caption');
        graphic.append(caption); row.append(graphic); section.append(row);
      }
      container.append(section);
    }
    container.scrollTop = scroll;
    if (previousEvent !== container.dataset.currentEvent) {
      const active = container.querySelector('[data-current="true"]');
      const bounds = container.getBoundingClientRect(), row = active?.getBoundingClientRect();
      const inset = container.querySelector('.walkthrough-sequence-head')?.getBoundingClientRect().height || 40;
      if (row && (row.top < bounds.top + inset || row.bottom > bounds.bottom)) {
        container.scrollTop += row.top - bounds.top - inset - 8;
      }
    }
    if (focus) [...container.querySelectorAll('[data-event-id]')].find(node => node.dataset.eventId === focus)?.focus({preventScroll: true});
  }
  function inspection(container, scene, frame, previous, selected) {
    container.replaceChildren();
    const node = scene.nodes.find(item => item.id === selected);
    if (node) {
      container.append(element('h4', node.label), element('p', frame.nodes[node.id] || '本例未记录额外状态。'));
    }
    if (!frame.index) { container.append(element('p', '当前为初始状态，尚无本次操作引起的变化。')); return; }
    const labels = Object.fromEntries(scene.structures.flatMap(structure =>
      structure.fields.filter(field => !field.hidden).map(field => [field.key, field.label])));
    const delta = changes(previous.values, frame.values, labels);
    const nodeDelta = changes(previous.nodes, frame.nodes,
      Object.fromEntries(scene.nodes.filter(item => !selected || item.id === selected).map(item => [item.id, item.label])));
    const list = element('dl', undefined, 'walkthrough-deltas');
    for (const change of [...delta, ...nodeDelta]) {
      const item = element('div'); item.dataset.changedField = change.key;
      const value = element('dd');
      value.append(element('span', change.before, 'walkthrough-value-before'), element('span', ' → '), element('strong', change.after));
      item.append(element('dt', change.label), value); list.append(item);
    }
    container.append(list.childElementCount ? list : element('p', '本步没有改变已列出的状态字段。'));
  }
  return {changes, groups, lanes, sequence, inspection};
});
