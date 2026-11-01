/* Offline walkthrough UI. All causal state and source references live in the scene. */
(function () {
  'use strict';
  const lab = document.querySelector('[data-vbr-lab]');
  const data = document.getElementById('virtio-blk-read-data');
  if (!lab || !data || !window.VirtioBlkReadModel) return;
  const catalogData = document.getElementById('block-io-scenarios-data');
  let baseScene, catalog, scene, frames;
  try {
    baseScene = JSON.parse(data.textContent);
    catalog = JSON.parse(catalogData.textContent);
    scene = window.VirtioBlkReadModel.getScenario(baseScene, 'read');
    frames = window.VirtioBlkReadModel.buildTrace(scene);
  } catch (error) {
    console.error('The virtio-blk walkthrough could not be initialized.', error);
    return; // Keep the complete, server-rendered reading fallback available.
  }

  const one = selector => lab.querySelector(selector);
  const all = selector => [...lab.querySelectorAll(selector)];
  const setText = (selector, value) => { one(selector).textContent = value; };
  const views = ['overview', 'mechanism', 'source'];
  const viewHelp = {
    overview: '按阶段查看请求跨层流转；中断通知、待注入和 Guest 处理分别标示。',
    mechanism: '直接查看描述符、环槽和数据缓冲区；每步显示操作后的状态，不表示实际耗时。',
    source: '源码路径：按执行上下文展开关键函数，高亮本步涉及的函数。辅助调用已省略；事件通知、线程切换和完成回调分别标明，不是一条同步调用栈。',
  };
  let phases = scene.phases.map(phase => ({...phase,
    end: frames.map(frame => frame.phase).lastIndexOf(phase.id)}));
  function overviewMilestones() {
    // Pending and actual delivery remain separate even at coarse resolution.
    const irq = ['pending-interrupt', 'inject-interrupt']
      .map(id => frames.findIndex(frame => frame.id === id)).filter(position => position >= 0);
    // An asynchronous backend can finish its data transfer before the phase's
    // final callback. Keep that transfer visible instead of skipping its edge.
    const transfers = frames.flatMap((frame, position) =>
      frame.edge?.kind === 'data' || frame.changes.dataReady === true ? [position] : []);
    return [...new Set([0, ...phases.map(phase => phase.end), ...irq, ...transfers])].sort((a, b) => a - b);
  }
  let overviewStops = overviewMilestones();
  const nodes = Object.fromEntries(all('[data-vbr-node]').map(node => [node.dataset.vbrNode, node]));
  const board = one('[data-vbr-board]');
  const svg = one('[data-vbr-connections]');
  const edge = one('[data-vbr-edge]');
  const traveler = one('[data-vbr-traveler]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let index = 0, view = 'overview', timer = null, animation = null, layoutFrame = null;
  let drawingVersion = 0;

  function loadScenario(operation, backend = scene.request.backend || 'local-blk') {
    scene = window.VirtioBlkReadModel.getScenario(baseScene, operation, backend, catalog);
    frames = window.VirtioBlkReadModel.buildTrace(scene);
    phases = scene.phases.map(phase => ({...phase,
      end: frames.map(frame => frame.phase).lastIndexOf(phase.id)}));
    overviewStops = overviewMilestones();
    const presentation = scene.presentation || {};
    one('[data-vbr-backend-select]').value = backend;
    setText('[data-vbr-device-domain]', presentation.deviceDomain || 'QEMU');
    setText('[data-vbr-device-context]', presentation.deviceContext || '宿主用户态 · IOThread');
    setText('[data-vbr-device-title]', presentation.deviceTitle || 'virtio-blk');
    setText('[data-vbr-device-summary]', presentation.deviceSummary
      || (presentation.notificationMode === 'poll'
        ? '轮询共享队列，经 bdev 提交 NVMe I/O；不经过 QEMU 块层。'
        : '设备模型与块层。通过映射访问同一份 Guest RAM。'));
    setText('[data-vbr-backend-title]', presentation.storageTitle || '本地文件');
    setText('[data-vbr-storage-context]', presentation.storageContext || 'QEMU 后端 / 宿主 I/O');
    setText('[data-vbr-storage-summary]', presentation.storageSummary
      || 'raw 格式与文件协议；cache.direct=off、aio=threads，可能命中宿主页缓存。');
    setText('[data-vbr-control-plane]', presentation.controlPlane || '');
    one('[data-vbr-control-plane]').hidden = !presentation.controlPlane;
    setText('[data-vbr-scenario-summary]', scene.summary);
    one('[data-vbr-scenario-summary]').hidden = false;
    setText('[data-vbr-queue-title]', presentation.queueTitle || 'virtqueue 0');
    setText('[data-vbr-notify-title]', presentation.notificationMode === 'poll' ? '提交路径不经 KVM' : '提交通知');
    setText('[data-vbr-completion-label]', scene.request.interface === 'virtio-scsi' ? 'SCSI response' : 'status');
    setText('[data-vbr-used-length-note]', scene.request.usedLengthNote || '');
    one('[data-vbr-used-length-note]').hidden = !scene.request.usedLengthNote;
    const assumptions = one('[data-vbr-assumptions]');
    assumptions.replaceChildren(...scene.assumptions.map(text => {
      const item = document.createElement('li'); item.textContent = text; return item;
    }));
    const baselines = one('[data-vbr-baselines]');
    baselines.replaceChildren(document.createTextNode('源码版本：'));
    scene.baselines.forEach((baseline, n) => {
      if (n) baselines.append(document.createTextNode(' · '));
      const link = document.createElement('a');
      link.href = baseline.url; link.textContent = `${baseline.project} ${baseline.version}`;
      link.target = '_blank'; link.rel = 'noopener noreferrer'; baselines.append(link);
    });
    one('[data-vbr-configuration]').hidden = false;
    all('[data-vbr-phase]').forEach(button => {
      button.querySelector('strong').textContent = phases.find(phase => phase.id === button.dataset.vbrPhase).title;
    });
    const paths = one('[data-vbr-callpaths]');
    paths.replaceChildren();
    for (const [number, path] of scene.callPaths.entries()) {
      const section = document.createElement('section');
      section.className = 'vbr-callpath';
      section.dataset.vbrCallpath = path.id;
      const title = document.createElement('h4');
      title.textContent = `${number + 1}. ${path.title}`;
      const context = document.createElement('p');
      context.className = 'vbr-callpath-context'; context.textContent = path.context;
      const list = document.createElement('ol');
      for (const id of path.sourceIds) {
        const source = scene.sources[id];
        const item = document.createElement('li'); item.dataset.vbrSource = id;
        const link = document.createElement('a');
        link.href = source.url; link.title = `${source.path}:${source.line}`;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        const symbol = document.createElement('strong'); symbol.textContent = source.symbol;
        const file = document.createElement('span'); file.textContent = link.title;
        link.append(symbol, file); item.append(link); list.append(item);
      }
      const boundary = document.createElement('p');
      boundary.className = 'vbr-callpath-boundary'; boundary.textContent = path.boundary;
      section.append(title, context, list, boundary); paths.append(section);
    }
  }

  function readLocation() {
    const params = new URL(window.location.href).searchParams;
    const backend = catalog.routes.some(route => route.id === params.get('backend'))
      ? params.get('backend') : 'local-blk';
    loadScenario(params.get('operation') === 'write' ? 'write' : 'read', backend);
    const requested = frames.findIndex(frame => frame.id === params.get('step'));
    index = requested < 0 ? 0 : requested;
    view = views.includes(params.get('view')) ? params.get('view') : 'overview';
  }

  function writeLocation() {
    const url = new URL(window.location.href);
    if (index) url.searchParams.set('step', frames[index].id);
    else url.searchParams.delete('step');
    if (view !== 'overview') url.searchParams.set('view', view);
    else url.searchParams.delete('view');
    if (scene.request.operation === 'write') url.searchParams.set('operation', 'write');
    else url.searchParams.delete('operation');
    if (scene.request.backend && scene.request.backend !== 'local-blk') url.searchParams.set('backend', scene.request.backend);
    else url.searchParams.delete('backend');
    // A static file may be opened directly; history updates are an enhancement.
    try { window.history.replaceState(null, '', url); } catch { /* Keep playback working. */ }
  }

  function nextIndex(direction) {
    if (view !== 'overview') return Math.max(0, Math.min(frames.length - 1, index + direction));
    return direction > 0 ? (overviewStops.find(stop => stop > index) ?? index)
      : ([...overviewStops].reverse().find(stop => stop < index) ?? index);
  }

  function stopAnimation() {
    drawingVersion += 1;
    if (animation !== null) window.cancelAnimationFrame(animation);
    animation = null;
    traveler.setAttribute('hidden', '');
  }

  function stopPlayback() {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    one('[data-vbr-play]').textContent = '播放';
    one('[data-vbr-play]').setAttribute('aria-pressed', 'false');
  }

  function announce(message) { setText('[data-vbr-announcement]', message); }

  // Choose an orthogonal route around labels rather than drawing across text.
  // Only the current causal relationship is shown, not a fixed all-layer pipeline.
  function routeConnection(from, to, width, height) {
    const origin = board.getBoundingClientRect();
    const rect = node => {
      const r = node.getBoundingClientRect();
      return {left: r.left - origin.left, right: r.right - origin.left,
        top: r.top - origin.top, bottom: r.bottom - origin.top};
    };
    const a = rect(from), b = rect(to);
    const obstacles = all('.vbr-node, .vbr-domain > header, .vbr-domain > p, .vbr-subdomain-label, .vbr-memory > h4').map(rect);
    const ports = r => [
      {x: (r.left + r.right) / 2, y: r.top - 3, nx: 0, ny: -1},
      {x: r.right + 3, y: (r.top + r.bottom) / 2, nx: 1, ny: 0},
      {x: (r.left + r.right) / 2, y: r.bottom + 3, nx: 0, ny: 1},
      {x: r.left - 3, y: (r.top + r.bottom) / 2, nx: -1, ny: 0},
    ];
    const xs = [7, width - 7, ...obstacles.flatMap(r => [r.left - 8, r.right + 8])]
      .filter(x => x >= 5 && x <= width - 5);
    const ys = [7, height - 7, ...obstacles.flatMap(r => [r.top - 8, r.bottom + 8])]
      .filter(y => y >= 5 && y <= height - 5);
    let best = null, lowest = Infinity;
    function consider(points) {
      let cost = points.length * 9;
      for (let n = 1; n < points.length; n += 1) {
        const p = points[n - 1], q = points[n];
        cost += Math.abs(p.x - q.x) + Math.abs(p.y - q.y);
        for (const r of obstacles) {
          const cross = Math.abs(p.x - q.x) < .1
            ? p.x > r.left - 1 && p.x < r.right + 1
              && Math.max(p.y, q.y) > r.top + 1 && Math.min(p.y, q.y) < r.bottom - 1
            : p.y > r.top - 1 && p.y < r.bottom + 1
              && Math.max(p.x, q.x) > r.left + 1 && Math.min(p.x, q.x) < r.right - 1;
          if (cross) cost += 10000;
        }
      }
      if (cost < lowest) { best = points; lowest = cost; }
    }
    for (const start of ports(a)) for (const end of ports(b)) {
      // Normal stubs make the arrow enter its target, not run along its border.
      const p = {x: start.x + start.nx * 7, y: start.y + start.ny * 7};
      const q = {x: end.x + end.nx * 7, y: end.y + end.ny * 7};
      if ([p, q].some(point => point.x < 2 || point.x > width - 2
          || point.y < 2 || point.y > height - 2)) continue;
      consider([start, p, {x: p.x, y: q.y}, q, end]);
      consider([start, p, {x: q.x, y: p.y}, q, end]);
      for (const x of xs) consider([start, p, {x, y: p.y}, {x, y: q.y}, q, end]);
      for (const y of ys) consider([start, p, {x: p.x, y}, {x: q.x, y}, q, end]);
    }
    return best.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  }

  function drawConnection(animate = false) {
    stopAnimation();
    if (view !== 'overview') { edge.setAttribute('d', ''); return; }
    const relation = frames[index].edge;
    if (!relation) { edge.setAttribute('d', ''); return; }
    const bounds = board.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    edge.dataset.kind = relation.kind;
    svg.dataset.kind = relation.kind;
    edge.setAttribute('d', routeConnection(nodes[relation.from], nodes[relation.to], bounds.width, bounds.height));
    if (!animate || reducedMotion.matches) return;
    const version = drawingVersion;
    const length = edge.getTotalLength();
    const started = performance.now();
    traveler.removeAttribute('hidden');
    function tick(now) {
      if (version !== drawingVersion) return;
      const progress = Math.min(1, (now - started) / 850);
      const point = edge.getPointAtLength(length * progress);
      traveler.setAttribute('cx', point.x);
      traveler.setAttribute('cy', point.y);
      if (progress < 1) animation = window.requestAnimationFrame(tick);
      else { animation = null; traveler.setAttribute('hidden', ''); }
    }
    animation = window.requestAnimationFrame(tick);
  }

  function scheduleDrawing(animate = false) {
    if (layoutFrame !== null) window.cancelAnimationFrame(layoutFrame);
    layoutFrame = window.requestAnimationFrame(() => {
      layoutFrame = null;
      drawConnection(animate);
    });
  }

  function renderSources(frame) {
    const list = one('[data-vbr-source-list]');
    list.replaceChildren();
    for (const id of frame.sourceIds) {
      const source = scene.sources[id];
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = source.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const name = document.createElement('strong');
      name.textContent = source.symbol;
      const location = document.createElement('span');
      location.textContent = `${source.project} · ${source.path}:${source.line}`;
      link.append(name, location);
      item.append(link);
      list.append(item);
    }
    all('[data-vbr-source]').forEach(node => {
      node.dataset.active = String(frame.sourceIds.includes(node.dataset.vbrSource));
    });
    all('[data-vbr-callpath]').forEach(node => {
      node.dataset.active = String(Boolean(node.querySelector('[data-vbr-source][data-active="true"]')));
    });
  }

  function render({animate = false, speak = false, updateURL = true} = {}) {
    const frame = frames[index], s = frame.state;
    const writing = scene.request.operation === 'write';
    const scsi = scene.request.interface === 'virtio-scsi';
    const polling = scene.presentation?.notificationMode === 'poll';
    const verb = writing ? '写入' : '读取';
    const syscall = writing ? 'pwrite' : 'pread';
    const phaseIndex = phases.findIndex(phase => phase.id === frame.phase);
    lab.dataset.view = view;
    lab.dataset.step = frame.id;
    lab.dataset.operation = scene.request.operation;
    lab.dataset.backend = scene.request.backend || 'local-blk';
    all('[data-vbr-operation]').forEach(button => button.setAttribute('aria-pressed',
      String(button.dataset.vbrOperation === scene.request.operation)));
    all('[data-vbr-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.vbrView === view)));
    all('[data-vbr-phase]').forEach(button => {
      if (button.dataset.vbrPhase === frame.phase) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
    one('[data-vbr-prev]').disabled = index === 0;
    one('[data-vbr-next]').disabled = index === frames.length - 1;
    one('[data-vbr-detail]').hidden = view === 'overview';
    one('[data-vbr-sources]').hidden = view !== 'source';
    one('[data-vbr-structure]').hidden = view !== 'mechanism';
    board.hidden = view !== 'overview';
    one('[data-vbr-callpaths]').hidden = view !== 'source';
    one('[data-vbr-legend]').hidden = view !== 'overview';
    one('[data-vbr-interrupt-path]').hidden = view === 'source';
    setText('[data-vbr-operation-note]', writing ? '4 KiB · 写入完成不等于持久化' : '4 KiB · 单请求成功路径');
    setText('[data-vbr-app-title]', `应用 · ${syscall}()`);
    setText('[data-vbr-storage-title]', `异步${verb}`);
    setText('[data-vbr-command]', scene.request.command || (writing ? 'VIRTIO_BLK_T_OUT' : 'VIRTIO_BLK_T_IN'));
    setText('[data-vbr-transfer]', writing ? 'Guest 用户缓冲区 → 后端' : '后端 → Guest 用户缓冲区');
    setText('[data-vbr-help]', viewHelp[view]);
    setText('[data-vbr-counter]', `阶段 ${phaseIndex + 1} / ${phases.length} · 步骤 ${index + 1} / ${frames.length}`);
    const lane = scene.lanes.find(item => item.id === frame.actor);
    setText('[data-vbr-actor]', `${lane.title} · ${lane.subtitle}`);
    setText('[data-vbr-title]', frame.title);
    setText('[data-vbr-description]', frame.description);
    setText('[data-vbr-detail]', frame.detail);
    // Explain diagram edges in the inspector, never repeat the step title below
    // the visualization. Detailed views already include the full explanation.
    const eventLabel = frame.edge?.label || '';
    setText('[data-vbr-event]', eventLabel);
    one('[data-vbr-event]').hidden = view !== 'overview' || !eventLabel
      || eventLabel === frame.title || frame.description.includes(eventLabel);
    renderSources(frame);

    const done = {application: s.userReturned, driver: s.prepared, queue: s.published, notify: s.notified,
      device: s.consumed, storage: s.dataReady, buffer: s.bufferReady,
      completion: s.usedPublished, interrupt: s.irqInjected, reclaim: s.completed};
    for (const [id, node] of Object.entries(nodes)) {
      node.dataset.active = String(frame.active.includes(id)
        || Boolean(frame.edge && [frame.edge.from, frame.edge.to].includes(id)));
      node.dataset.done = String(done[id]);
    }
    // A poller reads shared RAM directly; KVM is not a successful submission hop.
    nodes.notify.disabled = polling;
    if (polling) {
      nodes.notify.dataset.active = 'false';
      nodes.notify.dataset.done = 'false';
    }
    const captions = {
      application: s.userReturned ? `${syscall}() 返回 4096` : s.bioCompleted ? 'I/O 已完成，等待返回' : s.syscallEntered ? '内核处理中，尚未返回' : `4 KiB ${verb}待发起`,
      driver: s.completed ? 'R1 块请求已结束' : s.published ? 'R1 已提交，等待完成' : s.prepared ? '命令与直接链已准备' : s.blockSubmitted ? 'bio 已提交，形成 R1' : s.pagesMapped ? '用户页已固定，bio 已建立' : s.syscallEntered ? `处理 ${syscall}64` : 'R1 尚未形成',
      queue: s.published ? '链头 0 已发布 · 0 → 1 → 2' : s.prepared ? '链已填写，尚未发布' : '链 0 → 1 → 2 尚未发布',
      notify: polling ? 'NO_NOTIFY · 不发送 kick' : s.notified ? '已触发 ioeventfd' : 'ioeventfd · 等待 kick',
      device: s.usedPublished ? 'R1 完成已发布' : s.statusWritten ? '响应已回写，used 待发布'
        : s.dataReady ? '数据 I/O 完成，等待回调或回写' : s.submitted ? '请求保持，等待异步返回'
          : s.consumed ? '已取出 R1 · 整理 iovec' : polling ? 'vhost poller 轮询队列' : 'IOThread 等待队列',
      storage: s.dataReady ? (writing ? '写请求完成 · 未确认持久化' : '4 KiB 读取成功') : s.submitted ? `已提交${verb}，异步等待` : '偏移 1 MiB · 4 KiB',
      buffer: writing ? (s.userReturned ? '应用可复用缓冲区' : s.dataReady ? '数据已交付，等待请求返回' : s.bufferReady ? '4096 B 待写数据已准备' : '应用尚未准备写入数据')
        : s.userReturned ? '应用可读取 4096 B 结果' : s.bufferReady ? '数据已写入，应用尚未返回' : s.pagesMapped ? '用户页已固定 · 等待数据' : '用户缓冲区 · 尚未映射',
      completion: s.usedPublished ? 'used.idx = 1 · 完成可消费' : s.usedWritten ? '记录已写，idx 尚未发布' : s.statusWritten ? (scsi ? 'response = OK · status = GOOD' : 'status = OK · used 待发布') : '尚未发布完成',
      interrupt: s.guestNoticed ? 'Guest 已进入中断处理' : s.irqInjected ? 'VM-entry 已递送中断' : s.irqPending ? 'vLAPIC 挂起 · 等待注入' : s.irqRouted ? 'MSI 路由已匹配' : s.interruptSignaled ? 'irqfd 已触发 · 尚未注入' : 'irqfd → vLAPIC → VM-entry',
      reclaim: s.completed ? 'R1 完成 · 可继续使用资源' : s.reclaimed ? '链已回收，块请求待结束' : s.guestNoticed ? '检查 used，准备回收' : '等待设备完成',
    };
    for (const [id, value] of Object.entries(captions)) setText(`[data-vbr-node-state="${id}"]`, value);
    setText('[data-vbr-buffer-detail]', captions.buffer);
    const irqStates = [s.interruptSignaled, s.irqPending, s.irqInjected, s.guestNoticed];
    all('[data-vbr-irq-stage]').forEach((item, n) => {
      item.dataset.done = String(irqStates[n]);
      item.dataset.active = String(irqStates[n] && !irqStates[n + 1]);
    });
    all('[data-vbr-metric]').forEach(node => { node.textContent = s[node.dataset.vbrMetric]; });
    all('[data-vbr-byte]').forEach((cell, n) => {
      cell.dataset.filled = String(s.bufferReady);
      cell.style.setProperty('--vbr-byte-order', n);
    });
    for (const descriptor of scene.request.descriptors) {
      const box = one(`[data-vbr-descriptor="${descriptor.id}"]`);
      box.dataset.active = String(s.prepared && !s.reclaimed);
      setText(`[data-vbr-descriptor-role="${descriptor.id}"]`, descriptor.role);
      setText(`[data-vbr-descriptor-address="${descriptor.id}"]`, `${descriptor.addr} · ${descriptor.len} B`);
      setText(`[data-vbr-descriptor-flags="${descriptor.id}"]`, s.prepared ? descriptor.flags.join(' · ') : '尚未填写');
      setText(`[data-vbr-descriptor-next="${descriptor.id}"]`, s.prepared ? `next = ${descriptor.next ?? '—'}` : '尚未建立链');
    }
    for (const name of ['available', 'used']) {
      const written = name === 'available' ? s.published : s.usedWritten;
      all(`[data-vbr-ring="${name}"] .vbr-slot`).forEach((slot, n) => {
        slot.dataset.filled = String(n === 0 && written);
        slot.dataset.active = String(n === 0 && written && !s.reclaimed);
        slot.querySelector('span').textContent = n === 0 && written ? (name === 'available' ? '头 0' : 'id 0') : '—';
      });
    }
    setText('[data-vbr-status-byte]', s.statusWritten
      ? (scsi ? 'response = OK · status = GOOD · resid = 0 · sense_len = 0' : '0x00 · OK')
      : '未回写（初值不作保证）');
    setText('[data-vbr-used-length]', s.usedWritten
      ? `${s.usedLength} B${scsi ? '（实现记录值）' : writing ? '（仅状态）' : ' = 4096 + 1'}${s.usedPublished ? '' : '（待发布）'}`
      : '尚无完成记录');
    const ownership = s.userReturned ? (writing ? 'pwrite() 已返回，可复用缓冲区；本演示未执行 flush / FUA'
      : 'pread() 已返回，应用可读取结果')
      : s.completed ? '块请求结束，应用调用尚未返回'
        : s.bioCompleted ? 'bio 等待条件已满足，块请求与直接 I/O 路径仍需清理'
      : s.reclaimed ? '描述符已回收，数据映射待清理'
        : s.usedPublished ? '设备已完成，等待 Guest 消费完成'
          : s.published ? '设备可访问，驱动必须保持缓冲区有效'
            : s.pagesMapped ? '用户页已固定，尚未发布给设备' : '用户缓冲区尚未交给设备';
    setText('[data-vbr-ownership]', ownership);
    if (speak) announce(`${frame.title}。${frame.description}`);
    if (updateURL) writeLocation();
    scheduleDrawing(animate);
  }

  function move(target, manual = true) {
    if (manual) stopPlayback();
    const previous = index;
    index = Math.max(0, Math.min(frames.length - 1, target));
    // Reverse navigation restores snapshots; it never animates data travelling backwards.
    render({animate: index > previous, speak: manual});
    if (index === frames.length - 1) {
      stopPlayback();
      if (!manual) announce(`${scene.request.operation === 'write' ? '写入' : '读取'}过程播放完成，系统调用已返回。`);
    }
  }

  function schedulePlayback() {
    const delay = Number(one('[data-vbr-speed]').value);
    timer = window.setTimeout(() => {
      timer = null;
      move(nextIndex(1), false);
      if (index < frames.length - 1) schedulePlayback();
    }, [1200, 2200, 3200].includes(delay) ? delay : 2200);
  }

  one('[data-vbr-prev]').addEventListener('click', () => move(nextIndex(-1)));
  one('[data-vbr-next]').addEventListener('click', () => move(nextIndex(1)));
  one('[data-vbr-reset]').addEventListener('click', () => move(0));
  one('[data-vbr-play]').addEventListener('click', () => {
    if (timer !== null) { stopPlayback(); announce('已暂停。'); return; }
    if (index === frames.length - 1) { index = 0; render(); }
    one('[data-vbr-play]').textContent = '暂停';
    one('[data-vbr-play]').setAttribute('aria-pressed', 'true');
    announce('开始播放。可随时暂停或单步查看。');
    schedulePlayback();
  });
  one('[data-vbr-speed]').addEventListener('change', () => {
    if (timer !== null) { window.clearTimeout(timer); schedulePlayback(); }
  });
  all('[data-vbr-view]').forEach(button => button.addEventListener('click', () => {
    stopPlayback();
    view = button.dataset.vbrView;
    render({speak: false});
    announce(`已切换到${button.textContent}，当前步骤保持不变。`);
  }));
  all('[data-vbr-operation]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.vbrOperation === scene.request.operation) return;
    stopPlayback();
    loadScenario(button.dataset.vbrOperation);
    index = 0;
    render();
    announce(`已切换为${button.textContent}，从请求开始。`);
  }));
  one('[data-vbr-backend-select]').addEventListener('change', event => {
    stopPlayback();
    loadScenario(scene.request.operation, event.target.value);
    index = 0;
    render();
    announce(`已切换到${event.target.selectedOptions[0].textContent}，从请求开始。`);
  });
  all('[data-vbr-phase]').forEach(button => button.addEventListener('click', () => {
    move(frames.findIndex(frame => frame.phase === button.dataset.vbrPhase));
  }));
  const targetFlag = {application: 'syscallEntered', driver: 'blockSubmitted', queue: 'published', notify: 'notified',
    device: 'consumed', storage: 'submitted', buffer: 'dataReady', completion: 'usedPublished',
    interrupt: 'interruptSignaled', reclaim: 'reclaimed'};
  for (const [id, node] of Object.entries(nodes)) {
    node.disabled = false;
    node.addEventListener('click', () => move(frames.findIndex(frame => frame.changes[targetFlag[id]] === true)));
  }
  lab.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
        || /^(INPUT|SELECT|TEXTAREA|A)$/.test(event.target.tagName)) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault(); move(nextIndex(event.key === 'ArrowRight' ? 1 : -1));
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { stopPlayback(); stopAnimation(); }
  });
  window.addEventListener('pagehide', () => { stopPlayback(); stopAnimation(); });
  window.addEventListener('popstate', () => { stopPlayback(); readLocation(); render({updateURL: false}); });
  window.addEventListener('resize', () => scheduleDrawing());
  reducedMotion.addEventListener('change', () => scheduleDrawing());
  if (typeof ResizeObserver === 'function') new ResizeObserver(() => scheduleDrawing()).observe(board);
  if (typeof IntersectionObserver === 'function') new IntersectionObserver(entries => {
    if (!entries[0].isIntersecting) { stopPlayback(); stopAnimation(); }
  }).observe(lab);

  one('[data-vbr-controls]').hidden = false;
  one('[data-vbr-operation-controls]').hidden = false;
  one('[data-vbr-help]').hidden = false;
  one('[data-vbr-progress]').hidden = false;
  one('[data-vbr-fallback]').hidden = true;
  lab.dataset.initialized = 'true';
  readLocation();
  render({updateURL: false});

})();
