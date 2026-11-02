/* Offline execution walkthrough: independent scenarios, cumulative state, no live requests. */
(function () {
  'use strict';
  const lab = document.querySelector('[data-execution-lab]');
  const data = document.getElementById('execution-demo-data');
  const model = window.ExecutionDemoModel;
  const components = window.WalkthroughComponents;
  if (!lab || !data || !model) return;
  const one = selector => lab.querySelector(selector);
  const all = selector => [...lab.querySelectorAll(selector)];
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const setText = (selector, value) => { one(selector).textContent = value; };
  let demo;
  try { demo = JSON.parse(data.textContent); } catch (error) { return; }
  const views = ['overview', 'structure', 'source'];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let scene, trace, index = 0, view = 'overview', timer = null, layout = null, firstBranch = null;
  let animateNext = false;
  const controls = one('[data-execution-controls]');
  // Embedded persistence and the main disk walkthrough keep independent links.
  const urlKey = name => (lab.dataset.executionUrlPrefix || '') + name;
  const nodesBoard = one('[data-execution-nodes]');
  const contextsBoard = el('div');
  contextsBoard.dataset.executionContexts = '';
  nodesBoard.after(contextsBoard);
  let svg;

  // Options are curated scenario presets, not freely combinable simulation flags.
  const selector = demo.selector;
  const categories = selector.categories || [{id: '', groups: selector.groups}];
  const membership = new Map(categories.flatMap(category => category.groups.flatMap(group =>
    group.scenarios.map(option => [option.id, {category, group}]))));

  function selectorGroups(category, operation) {
    return category.groups.filter(group => !selector.operations || group.operation === operation);
  }

  function syncSelector() {
    const {category, group} = membership.get(scene.id);
    const categoryControl = one('[data-execution-category]');
    if (categoryControl) categoryControl.value = category.id;
    all('[data-execution-operation]').forEach(button =>
      button.setAttribute('aria-pressed', String(button.dataset.executionOperation === group.operation)));
    const groups = selectorGroups(category, group.operation);
    const options = groups.flatMap(item => item.scenarios);
    const scenarioControl = one('[data-execution-scenario]');
    scenarioControl.replaceChildren(...groups.flatMap(item => {
      const children = item.scenarios.map(option => {
        const node = el('option', option.label); node.value = option.id; return node;
      });
      if (groups.length === 1) return children;
      const optgroup = el('optgroup'); optgroup.label = item.label;
      optgroup.append(...children); return [optgroup];
    }));
    scenarioControl.value = scene.id;
    one('[data-execution-scenario-choice]').hidden = Boolean(selector.categories && options.length === 1);
  }

  function selectScenario(id) {
    pause(); firstBranch = null; buildScene(id); index = 0; render(true);
  }

  function sourceLink(id) {
    return components.sourceLink(demo.sources[id]);
  }

  function buildScene(id) {
    scene = demo.scenarios.find(item => item.id === id)
      || demo.scenarios.find(item => item.id === demo.defaultScenario);
    firstBranch = scene.parallel?.branches.find(branch => branch.id === firstBranch)?.id
      || scene.parallel?.branches[0].id || null;
    trace = model.buildTrace(scene, firstBranch);
    lab.dataset.presentation = scene.presentation || 'domains';
    syncSelector();
    setText('[data-execution-summary]', scene.summary);
    one('[data-execution-assumptions]').replaceChildren(...scene.assumptions.map(text => el('li', text)));
    const makeNode = id => {
      const node = scene.nodes.find(item => item.id === id);
      const card = el('div', undefined, 'execution-node'); card.dataset.executionNode = node.id;
      const heading = el('div', undefined, 'execution-node-heading');
      heading.append(el('strong', node.label));
      if (node.badge) heading.append(el('span', node.badge, 'walkthrough-node-badge'));
      card.append(heading, el('span', '', 'execution-node-status'));
      return card;
    };
    const domains = el('div', undefined, 'walkthrough-domains');
    const lanes = scene.layout.domains.filter(group => group.nodes.length);
    domains.style.setProperty('--domain-count', lanes.reduce((sum, group) => sum + (group.span || 1), 0));
    for (const group of lanes) {
      const domain = el('section', undefined, 'walkthrough-domain');
      domain.dataset.executionDomain = group.id;
      if (group.component) domain.dataset.component = group.component;
      if (group.span) domain.style.gridColumn = `span ${group.span}`;
      if (group.inline) domain.classList.add('walkthrough-domain--inline');
      const header = el('header'); header.append(el('small', group.context), el('h4', group.title));
      const nodes = el('div', undefined, 'walkthrough-domain-nodes');
      nodes.append(...group.nodes.map(makeNode));
      if (group.note) nodes.append(el('p', group.note, 'walkthrough-domain-note'));
      domain.append(header, nodes); domains.append(domain);
    }
    nodesBoard.replaceChildren(domains);
    components.contexts(contextsBoard, scene.layout.domains.filter(group => !group.nodes.length));
    if (scene.layout.memory) {
      const group = scene.layout.memory, memory = el('section', undefined, 'walkthrough-memory');
      const heading = el('h4', group.title); heading.append(el('span', group.description));
      const cells = el('div', undefined, 'walkthrough-memory-grid');
      cells.style.setProperty('--memory-count', group.nodes.length);
      cells.append(...group.nodes.map(id => {
        if (demo.id !== 'network-io' || id !== 'queue') return makeNode(id);
        const card = el('div', undefined, 'execution-network-memory'); card.dataset.executionNode = id;
        card.append(el('span', '', 'execution-node-status'));
        const visual = el('div'); visual.dataset.executionNetworkMemory = ''; card.append(visual);
        return card;
      })); memory.append(heading, cells); nodesBoard.append(memory);
    }
    if (scene.queue && demo.id !== 'network-io') {
      const queue = el('section'); queue.dataset.executionTypedQueue = 'compact';
      (nodesBoard.querySelector('.walkthrough-memory') || nodesBoard).append(queue);
    }
    svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('aria-hidden', 'true'); svg.classList.add('execution-arrows');
    nodesBoard.append(svg);
    const structures = one('[data-execution-structures]');
    structures.replaceChildren(...scene.structures.filter(structure =>
      structure.fields.some(field => !field.hidden)).map(structure => {
      const section = el('section', undefined, 'execution-structure');
      section.dataset.kind = structure.kind;
      section.append(el('h4', structure.title));
      if (scene.queue && structure.id === scene.queue.structureId) {
        section.dataset.executionTypedQueue = 'full';
        return section;
      }
      if (structure.kind === 'queue' && structure.fields.some(field => field.key === 'availIdx')) {
        const queue = el('div', undefined, 'execution-queue');
        const chain = el('div', undefined, 'execution-chain'); chain.dataset.executionChain = '';
        queue.append(chain);
        for (const name of ['avail', 'used']) {
          const ring = el('section', undefined, 'execution-ring'); ring.dataset.executionRing = name;
          ring.append(el('h5', name === 'avail' ? 'Available · 驱动发布' : 'Used · 设备完成'));
          const slots = el('div', undefined, 'execution-ring-slots');
          for (let n = 0; n < 8; n++) {
            const slot = el('div'); slot.append(el('small', String(n)), el('span', '—')); slots.append(slot);
          }
          ring.append(slots, el('p', '', 'execution-ring-position'), el('p', '', 'execution-ring-record')); queue.append(ring);
        }
        queue.append(el('p', '8 个槽位仅表示循环索引。环中保存链头与完成记录，报文位于描述符指向的 Guest 缓冲区。', 'execution-ring-note'));
        section.append(queue);
      }
      const list = el('dl', undefined, 'execution-fields');
      // The ring drawing already presents these fields, without repeating a table.
      list.hidden = Boolean(section.querySelector('.execution-queue'));
      structure.fields.filter(field => !field.hidden).forEach(field => {
        const cell = el('div'); cell.dataset.executionField = field.key;
        cell.append(el('dt', field.label), el('dd', field.initial)); list.append(cell);
      });
      section.append(list); return section;
    }));
    const sources = one('[data-execution-sources]');
    if (scene.callPaths) {
      components.sourcePaths(sources, scene.callPaths, demo.sources, scene.sourceSteps || []);
    } else {
    sources.replaceChildren(el('p', '按执行阶段列出实现入口。跨线程通知、硬件行为与回调分别标明；连线表示事件先后，不代表同一条调用栈。'));
    trace.slice(1).forEach(frame => {
      const row = el('section', undefined, 'execution-source-row'); row.dataset.executionSourceStep = frame.id;
      row.append(el('small', frame.actor), el('h4', frame.title));
      const list = el('ul');
      frame.sourceIds.forEach(id => { const item = el('li'); item.append(sourceLink(id)); list.append(item); });
      row.append(list); sources.append(row);
    });
    }
    progress();
  }

  function renderTimeline(frame) {
    const timeline = one('[data-execution-timeline]');
    const active = scene.presentation === 'cpu-timeline';
    timeline.hidden = !active; nodesBoard.hidden = active;
    if (!active) return;
    timeline.replaceChildren();
    const lanes = el('ol', undefined, 'walkthrough-cpu-lanes');
    for (const lane of scene.timeline.lanes) {
      const node = el('li', lane.label); node.dataset.current = String(frame.execution?.lane === lane.id);
      node.dataset.component = lane.id.startsWith('guest-') ? 'guest' : lane.id;
      lanes.append(node);
    }
    timeline.append(el('h4', '同一个 vCPU · 当前执行位置'), lanes);
    const instruction = el('div', undefined, 'walkthrough-cpu-instruction');
    instruction.append(el('small', frame.execution?.phase || '准备执行'),
      el('code', frame.execution?.instruction || '等待下一步'));
    timeline.append(instruction);
    const fields = el('dl', undefined, 'execution-fields');
    const labels = Object.fromEntries(scene.structures.flatMap(s => s.fields.map(f => [f.key, f.label])));
    for (const key of scene.timeline.fields) {
      const cell = el('div'); cell.dataset.current = String(frame.changed.includes(key));
      cell.append(el('dt', labels[key] || key), el('dd', frame.values[key])); fields.append(cell);
    }
    timeline.append(fields);
    const steps = el('ol', undefined, 'walkthrough-cpu-history');
    for (let n = Math.max(0, index - 1); n <= Math.min(trace.length - 1, index + 1); n++) {
      const item = el('li'); item.dataset.current = String(n === index);
      const lane = scene.timeline.lanes.find(lane => lane.id === trace[n].execution?.lane);
      item.append(el('small', n < index ? '上一步' : n === index ? '当前' : '下一步'),
        el('strong', trace[n].title), el('span', lane?.label || trace[n].actor));
      steps.append(item);
    }
    timeline.append(steps);
  }

  function progress() {
    const nav = one('[data-execution-progress]');
    nav.setAttribute('aria-label', view === 'structure' ? '关键结构步骤' : '演示阶段');
    const entries = model.navigation(scene, trace, view);
    const branched = scene.parallel && view !== 'structure';
    nav.classList.toggle('walkthrough-progress--branched', Boolean(branched));
    const makeButton = (entry, ordinal, prefix = '') => {
      const button = el('button'); button.type = 'button'; button.dataset.executionStep = String(entry.index);
      button.dataset.executionEnd = String(entry.end);
      if (view !== 'structure') button.dataset.executionPhase = entry.id;
      button.append(el('span', view === 'structure' && entry.index === 0 ? '○'
        : prefix + String(ordinal + (view === 'structure' ? 0 : 1)).padStart(2, '0')), el('strong', entry.title));
      button.addEventListener('click', () => move(entry.index)); return button;
    };
    if (!branched) { nav.replaceChildren(...entries.map((entry, n) => makeButton(entry, n))); return; }
    const common = el('div', undefined, 'walkthrough-common-phases');
    common.append(...entries.filter(entry => !entry.branch).map((entry, n) => makeButton(entry, n)));
    const fork = el('section', undefined, 'walkthrough-fork');
    fork.dataset.executionFork = '';
    const heading = el('header');
    heading.append(el('strong', scene.parallel.title), el('span', scene.parallel.description));
    const branches = el('div', undefined, 'walkthrough-branches');
    scene.parallel.branches.forEach((branch, ordinal) => {
      const section = el('section', undefined, 'walkthrough-branch'); section.dataset.executionBranch = branch.id;
      const header = el('header'), choose = el('button', '先观察此分支', 'walkthrough-branch-order');
      choose.type = 'button'; choose.dataset.executionOrder = branch.id;
      choose.setAttribute('aria-pressed', String(firstBranch === branch.id));
      choose.setAttribute('aria-label', `从分叉处先观察${branch.title}`);
      choose.addEventListener('click', () => {
        pause(); firstBranch = branch.id; buildScene(scene.id);
        index = trace.findIndex(frame => frame.id === scene.parallel.after);
        render(true);
      });
      header.append(el('strong', `${String.fromCharCode(65 + ordinal)} · ${branch.title}`), choose);
      const phases = el('div', undefined, 'walkthrough-branch-phases');
      phases.append(...entries.filter(entry => entry.branch === branch.id)
        .map((entry, n) => makeButton(entry, n, String.fromCharCode(65 + ordinal))));
      section.append(header, el('p', branch.summary), phases); branches.append(section);
    });
    fork.append(heading, branches); nav.replaceChildren(common, fork);
  }

  function draw() {
    const animate = animateNext; animateNext = false;
    if (layout) cancelAnimationFrame(layout);
    layout = requestAnimationFrame(() => {
      layout = null;
      if (view !== 'overview' || !svg || scene.presentation === 'cpu-timeline') return;
      const rect = nodesBoard.getBoundingClientRect();
      svg.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`); svg.replaceChildren();
      const ns = 'http://www.w3.org/2000/svg';
      const bounds = Object.fromEntries(all('[data-execution-node]').map(node => {
        const box = node.getBoundingClientRect();
        return [node.dataset.executionNode, {left: box.left - rect.left, right: box.right - rect.left,
          top: box.top - rect.top, bottom: box.bottom - rect.top}];
      }));
      // Route a queue handoff to the structure touched by this step, not the
      // entire Guest RAM rectangle. Other panels remain routing obstacles.
      const memoryBounds = {};
      all('[data-network-memory-part]').forEach(node => {
        const box = node.getBoundingClientRect();
        memoryBounds[node.dataset.networkMemoryPart] = {left: box.left - rect.left, right: box.right - rect.left,
          top: box.top - rect.top, bottom: box.bottom - rect.top};
      });
      if (memoryBounds.descriptors) {
        const keys = trace[index].queueState?.changed || [];
        const part = keys.some(key => key.startsWith('used.')) ? 'completion'
          : keys.includes('buffers') && !keys.includes('descriptors') ? 'buffers' : 'descriptors';
        bounds.queue = memoryBounds[part];
      }
      const textObstacles = all('.walkthrough-domain header, .walkthrough-memory > h4, .execution-network-memory > .execution-node-status').map(node => {
        const box = node.getBoundingClientRect();
        return {left:box.left-rect.left,right:box.right-rect.left,top:box.top-rect.top,bottom:box.bottom-rect.top};
      }).filter(box => box.right > box.left && box.bottom > box.top);
      for (const id of trace[index].links) {
        const edge = scene.links.find(link => link.id === id);
        const route = window.WalkthroughRouting?.route(bounds[edge.from], bounds[edge.to],
          [...Object.values(bounds), ...Object.values(memoryBounds), ...textObstacles], {width: rect.width, height: rect.height});
        if (!route) continue; // The text relationship remains available if space is obstructed.
        const d = route.map((point, n) => `${n ? 'L' : 'M'} ${point.join(' ')}`).join(' ');
        const [x2, y2] = route.at(-1), previous = route.at(-2);
        const angle = Math.atan2(y2 - previous[1], x2 - previous[0]);
        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d', d); path.dataset.kind = edge.kind; svg.append(path);
        const arrow = document.createElementNS(ns, 'path');
        const points = [Math.PI / 6, -Math.PI / 6].map(offset =>
          [x2 - 7 * Math.cos(angle + offset), y2 - 7 * Math.sin(angle + offset)]);
        arrow.setAttribute('d', `M ${points[0].join(' ')} L ${x2} ${y2} L ${points[1].join(' ')}`);
        arrow.dataset.kind = edge.kind; svg.append(arrow);
        if (animate && !reduced.matches && index > 0 && edge.kind === 'data') {
          const dot = document.createElementNS(ns, 'circle'); dot.setAttribute('r', '3');
          dot.setAttribute('class', 'walkthrough-packet');
          const motion = document.createElementNS(ns, 'animateMotion');
          motion.setAttribute('path', d); motion.setAttribute('dur', '.7s');
          motion.setAttribute('repeatCount', '1'); motion.setAttribute('fill', 'freeze');
          dot.append(motion); svg.append(dot);
          motion.beginElement?.();
          setTimeout(() => dot.remove(), 750);
        }
      }
    });
  }

  function writeLocation() {
    const url = new URL(location.href);
    url.searchParams.set(urlKey('scenario'), scene.id); url.searchParams.set(urlKey('view'), view);
    url.searchParams.set(urlKey('step'), trace[index].id);
    if (firstBranch) url.searchParams.set(urlKey('order'), firstBranch);
    else url.searchParams.delete(urlKey('order'));
    try { history.replaceState(null, '', url); } catch { /* Playback also works from local files. */ }
  }

  function render(speak = false, updateURL = true) {
    const frame = trace[index];
    renderTimeline(frame);
    one('.execution-key').textContent = scene.presentation === 'cpu-timeline'
      ? '高亮泳道表示当前执行位置，字段高亮表示本步变化。普通指令由物理 CPU 执行；不是在 Guest、KVM 与 QEMU 之间逐条搬运。'
      : '实线：数据 · 虚线：通知 · 点线：控制切换 · 蓝色映射：地址转换。高亮表示本步操作后的状态，动画不表示真实耗时。';
    one('.execution-key').hidden = view !== 'overview';
    lab.dataset.scenario = scene.id; lab.dataset.view = view; lab.dataset.step = frame.id;
    all('[data-execution-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.executionView === view)));
    all('[data-execution-step]').forEach(button => {
      const current = Number(button.dataset.executionStep) <= index && index <= Number(button.dataset.executionEnd);
      button.setAttribute('aria-current', current ? 'step' : 'false');
      button.dataset.done = String(Number(button.dataset.executionEnd) < index);
    });
    one('[data-execution-overview]').hidden = view !== 'overview';
    one('[data-execution-structures]').hidden = view !== 'structure';
    one('[data-execution-sources]').hidden = view !== 'source';
    setText('[data-execution-help]', {
      overview: '按阶段查看完整流程；播放和单步会依次展示阶段内的操作，箭头标出本步交接。',
      structure: '只保留与关键结构相关的步骤。高亮本步更新的字段，索引、地址和状态可逐步对照。',
      source: '按阶段定位实现；单步查看具体操作，点击函数或规范链接查看固定版本原文。',
    }[view]);
    if (scene.presentation === 'cpu-timeline' && view === 'overview') setText('[data-execution-help]', '沿同一个 vCPU 观察当前指令、寄存器与执行位置；Guest 权限切换、VM-exit 和返回 QEMU 分别标示。');
    if (scene.parallel && view !== 'structure') setText('[data-execution-help]', '共同前序完成后，两条分支独立推进。可切换先观察哪条分支；播放顺序不表示两条路径之间存在等待关系。');
    const stops = model.stops(trace, view), count = stops.length - 1;
    const stages = model.phases(scene, trace), phaseIndex = stages.findIndex(phase => phase.index <= index && index <= phase.end);
    const phase = stages[phaseIndex];
    const firstAction = Math.max(1, phase.index);
    const peers = stages.filter(item => item.branch === phase.branch);
    const branch = scene.parallel?.branches.find(item => item.id === phase.branch);
    const phaseLabel = branch ? `${branch.title} · 阶段 ${peers.indexOf(phase) + 1} / ${peers.length}`
      : `${scene.parallel ? '共同前序' : '阶段'} ${peers.indexOf(phase) + 1} / ${peers.length}`;
    setText('[data-execution-counter]', view === 'structure'
      ? (index === 0 ? `准备前 · 共 ${count} 步` : `第 ${stops.indexOf(index)} / ${count} 步`)
      : (index === 0 ? (scene.parallel ? `准备前 · ${peers.length} 个共同阶段，随后分两路` : `准备前 · 共 ${stages.length} 个阶段`)
        : `${phaseLabel} · 阶段内第 ${index - firstAction + 1} / ${phase.end - firstAction + 1} 步`));
    all('[data-execution-branch]').forEach(node => {
      node.dataset.current = String(node.dataset.executionBranch === phase.branch);
    });
    setText('[data-execution-actor]', frame.actor);
    setText('[data-execution-title]', frame.title);
    components.explanation(one('[data-step-explanation]'), {
      description: frame.description, detail: frame.detail,
    });
    all('[data-execution-node]').forEach(node => {
      node.dataset.active = String(frame.active.includes(node.dataset.executionNode));
      node.querySelector('.execution-node-status').textContent = frame.nodes[node.dataset.executionNode];
    });
    all('[data-execution-field]').forEach(node => {
      node.dataset.current = String(frame.changed.includes(node.dataset.executionField));
      node.querySelector('dd').textContent = frame.values[node.dataset.executionField];
    });
    if (scene.queue && frame.queueState) {
      const networkMemory = one('[data-execution-network-memory]');
      if (networkMemory) components.networkMemory(networkMemory, frame.queueState, scene.queue, frame);
      all('[data-execution-typed-queue]').forEach(node =>
        components.queue(node, frame.queueState, scene.queue, node.dataset.executionTypedQueue === 'compact'));
    }
    if (scene.callPaths) components.highlightSources(one('[data-execution-sources]'), frame.sourceIds, frame.id, demo.sources);
    all('[data-execution-chain]').forEach(node => {
      node.textContent = `描述符链 · ${frame.values.desc}`;
      node.dataset.current = String(frame.changed.includes('desc'));
    });
    all('[data-execution-ring]').forEach(ring => {
      const used = ring.dataset.executionRing === 'used';
      const producer = Number(frame.values[used ? 'usedIdx' : 'availIdx']);
      const consumer = Number(frame.values[used ? 'reclaimedIdx' : 'consumedIdx']);
      const recordKey = used ? 'usedEntry' : 'availHead';
      const producerKey = used ? 'usedIdx' : 'availIdx';
      const consumerKey = used ? 'reclaimedIdx' : 'consumedIdx';
      const records = Array(8).fill(null);
      for (const snapshot of trace.slice(0, index + 1)) {
        const value = snapshot.changes[recordKey];
        const slotMatch = value?.match(/slot\s+(\d+)/);
        if (slotMatch) records[Number(slotMatch[1]) % 8] = value;
      }
      const changedSlot = frame.changes[recordKey]?.match(/slot\s+(\d+)/);
      const slots = [...ring.querySelector('.execution-ring-slots').children];
      slots.forEach((slot, n) => {
        const record = records[n];
        const head = record?.match(used ? /id\s*:\s*(\d+)/ : /head\s+(\d+)/)?.[1];
        const changed = (changedSlot && Number(changedSlot[1]) % 8 === n)
          || (frame.changed.includes(producerKey) && (producer - 1) % 8 === n)
          || (frame.changed.includes(consumerKey) && (consumer - 1) % 8 === n);
        slot.querySelector('span').textContent = record ? `${used ? 'id' : '头'} ${head ?? '—'}` : '—';
        slot.title = record || '空槽';
        slot.dataset.filled = String(Boolean(record));
        slot.dataset.current = String(Boolean(record && changed));
        slot.dataset.returned = String(Boolean(record && consumer > n));
      });
      ring.querySelector('.execution-ring-position').textContent = `${used ? 'used' : 'avail'}.idx = ${producer} · ${used ? '驱动回收' : '设备消费'}位置 = ${consumer}`;
      ring.querySelector('.execution-ring-position').dataset.current = String(frame.changed.includes(producerKey) || frame.changed.includes(consumerKey));
      ring.querySelector('.execution-ring-record').textContent = frame.values[recordKey] === '空' ? '尚无记录' : frame.values[recordKey];
      ring.querySelector('.execution-ring-record').dataset.current = String(frame.changed.includes(recordKey));
    });
    all('[data-execution-source-step]').forEach(node => {
      node.dataset.current = String(node.dataset.executionSourceStep === frame.id);
    });
    const edges = frame.links.map(id => scene.links.find(link => link.id === id));
    one('[data-execution-connections]').replaceChildren(...(edges.length ? edges.map(edge => {
      const row = el('p'); row.dataset.kind = edge.kind;
      const from = scene.nodes.find(node => node.id === edge.from).label;
      const to = scene.nodes.find(node => node.id === edge.to).label;
      row.append(el('strong', from), el('span', ` → ${edge.label} → `), el('strong', to)); return row;
    }) : []));
    one('[data-execution-connections]').hidden = !edges.length || view !== 'overview' || scene.presentation === 'cpu-timeline';
    one('[data-execution-prev]').disabled = index === 0;
    one('[data-execution-next]').disabled = index === stops.at(-1);
    if (speak) setText('[data-execution-status]', `${frame.title}。${frame.description}`);
    const progressBar = one('[data-execution-progress]');
    const current = progressBar.querySelector('[aria-current="step"]');
    if (current) {
      const parent = progressBar.getBoundingClientRect(), box = current.getBoundingClientRect();
      if (box.left < parent.left) progressBar.scrollLeft -= parent.left - box.left;
      else if (box.right > parent.right) progressBar.scrollLeft += box.right - parent.right;
    }
    if (view === 'source') {
      const panel = one('[data-execution-sources]');
      const currentSource = panel.querySelector('[data-current="true"]');
      if (index === 0) panel.scrollTop = 0;
      else if (currentSource) {
        const parent = panel.getBoundingClientRect(), box = currentSource.getBoundingClientRect();
        if (box.top < parent.top || box.bottom > parent.bottom) panel.scrollTop += box.top - parent.top;
      }
    }
    // Optional protocol visuals share this snapshot with the standard controls.
    lab.executionSnapshot = {demo, scene, frame, view, firstBranch};
    lab.dispatchEvent(new CustomEvent('execution:frame', {detail: lab.executionSnapshot}));
    if (updateURL) writeLocation(); draw();
  }

  function pause() {
    clearTimeout(timer); timer = null;
    svg?.querySelectorAll('.walkthrough-packet').forEach(node => node.remove());
    setText('[data-execution-play]', '播放'); one('[data-execution-play]').setAttribute('aria-pressed', 'false');
  }
  function move(position, stop = true) {
    if (stop) pause();
    animateNext = position > index;
    index = model.project(trace, view, Math.max(0, Math.min(trace.length - 1, position)));
    render(stop);
  }
  function schedule() {
    timer = setTimeout(() => {
      move(model.next(trace, view, index, 1), false);
      if (index === model.stops(trace, view).at(-1)) { pause(); setText('[data-execution-status]', '当前视图演示完成。'); }
      else schedule();
    }, Number(one('[data-execution-speed]').value));
  }
  function loadLocation() {
    pause(); const params = new URL(location.href).searchParams;
    view = views.includes(params.get(urlKey('view'))) ? params.get(urlKey('view')) : 'overview';
    firstBranch = params.get(urlKey('order'));
    buildScene(params.get(urlKey('scenario')));
    index = model.project(trace, view, Math.max(0, trace.findIndex(frame => frame.id === params.get(urlKey('step')))));
    render(false, false);
  }
  lab.addEventListener('walkthrough:expand', pause);
  lab.addEventListener('walkthrough:resize', draw);
  lab.addEventListener('keydown', event => {
    if (event.target.classList.contains('execution-canvas') && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
      event.preventDefault(); move(model.next(trace, view, index, event.key === 'ArrowRight' ? 1 : -1));
    }
  });
  all('[data-execution-view]').forEach(button => button.addEventListener('click', () => {
    pause(); view = button.dataset.executionView;
    index = model.project(trace, view, index); progress(); render(true);
  }));
  one('[data-execution-scenario]').addEventListener('change', event => {
    selectScenario(event.target.value);
  });
  one('[data-execution-category]')?.addEventListener('change', event => {
    const category = categories.find(item => item.id === event.target.value);
    const operation = membership.get(scene.id).group.operation;
    selectScenario(selectorGroups(category, operation)[0].scenarios[0].id);
  });
  all('[data-execution-operation]').forEach(button => button.addEventListener('click', () => {
    const {category, group} = membership.get(scene.id);
    if (button.dataset.executionOperation === group.operation) return;
    selectScenario(selectorGroups(category, button.dataset.executionOperation)[0].scenarios[0].id);
  }));
  one('[data-execution-prev]').addEventListener('click', () => move(model.next(trace, view, index, -1)));
  one('[data-execution-next]').addEventListener('click', () => move(model.next(trace, view, index, 1)));
  one('[data-execution-reset]').addEventListener('click', () => move(0));
  one('[data-execution-play]').addEventListener('click', () => {
    if (timer) { pause(); return; }
    if (index === model.stops(trace, view).at(-1)) move(0);
    setText('[data-execution-play]', '暂停'); one('[data-execution-play]').setAttribute('aria-pressed', 'true'); schedule();
  });
  one('[data-execution-speed]').addEventListener('change', () => { if (timer) { clearTimeout(timer); schedule(); } });
  window.addEventListener('popstate', loadLocation);
  document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
  reduced.addEventListener('change', () => pause());
  window.addEventListener('resize', draw);
  if (window.ResizeObserver) new ResizeObserver(draw).observe(nodesBoard);
  try {
    loadLocation(); controls.hidden = false; one('[data-execution-fallback]').hidden = true; draw();
  } catch (error) {
    pause(); controls.hidden = true; one('[data-execution-fallback]').hidden = false;
    console.error('Execution walkthrough initialization failed.', error);
  }
})();
