/* Source-reviewed teaching sequence, not a VM emulator or measured trace. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.VirtioBlkReadModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const FLAGS = ['bufferReady', 'syscallEntered', 'pagesMapped', 'blockSubmitted',
    'prepared', 'published', 'notified', 'consumed', 'submitted',
    'dataReady', 'statusWritten', 'usedWritten', 'usedPublished',
    'interruptSignaled', 'irqRouted', 'irqPending', 'irqInjected',
    'guestNoticed', 'reclaimed', 'bioCompleted', 'completed', 'userReturned'];
  const COUNTERS = ['availIndex', 'backendIndex', 'usedIndex', 'driverIndex', 'freeDescriptors'];
  const STATE_KEYS = [...FLAGS, ...COUNTERS, 'statusByte', 'usedId', 'usedLength'];
  const NODES = ['application', 'driver', 'queue', 'notify', 'device', 'storage', 'buffer',
    'completion', 'interrupt', 'reclaim'];
  const LANES = ['guest-user', 'guest', 'kvm', 'qemu', 'spdk', 'ceph', 'backend', 'memory'];
  const REPOSITORIES = {QEMU: 'qemu/qemu', Linux: 'torvalds/linux', SPDK: 'spdk/spdk', Ceph: 'ceph/ceph'};
  const PRECONDITIONS = {
    pagesMapped: 'syscallEntered', blockSubmitted: 'pagesMapped', prepared: 'blockSubmitted',
    published: 'prepared', notified: 'published', consumed: 'notified',
    submitted: 'consumed', dataReady: 'submitted', statusWritten: 'dataReady',
    usedWritten: 'statusWritten', usedPublished: 'usedWritten',
    interruptSignaled: 'usedPublished', irqRouted: 'interruptSignaled',
    irqPending: 'irqRouted', irqInjected: 'irqPending', guestNoticed: 'irqInjected',
    reclaimed: 'guestNoticed', bioCompleted: 'reclaimed', completed: 'bioCompleted',
    userReturned: 'completed',
  };

  function requireThat(condition, message) {
    if (!condition) throw new TypeError(`Invalid virtio-blk scenario: ${message}`);
  }

  function record(value, label) {
    requireThat(value !== null && typeof value === 'object' && !Array.isArray(value), label);
    requireThat([Object.prototype, null].includes(Object.getPrototypeOf(value)), label);
    return value;
  }

  function text(value, label) {
    requireThat(typeof value === 'string' && value.trim().length > 0, label);
  }

  function id(value, label) {
    requireThat(typeof value === 'string' && /^[a-z][a-z0-9-]*$/.test(value), label);
  }

  function uniqueList(items, label) {
    requireThat(Array.isArray(items) && items.length > 0, label);
    requireThat(new Set(items).size === items.length, `${label} duplicates`);
  }

  function requestLayout(request) {
    const transport = request.interface ?? 'virtio-blk';
    requireThat(['virtio-blk', 'virtio-scsi'].includes(transport), 'request interface');
    const headerBytes = request.headerBytes ?? (transport === 'virtio-scsi' ? 51 : 16);
    const responseBytes = request.responseBytes ?? request.completionBytes
      ?? (transport === 'virtio-scsi' ? 108 : 1);
    for (const [label, value] of [['header bytes', headerBytes], ['response bytes', responseBytes]]) {
      requireThat(Number.isSafeInteger(value) && value > 0 && value <= 65536, label);
    }
    if (Object.hasOwn(request, 'completionBytes')) {
      requireThat(request.completionBytes === responseBytes, 'completion bytes mismatch');
    }
    if (transport === 'virtio-blk') {
      requireThat(headerBytes === 16 && responseBytes === 1, 'virtio-blk header/status size');
    }
    const isRead = request.operation === 'read';
    const scsiRead = transport === 'virtio-scsi' && isRead;
    const lengths = scsiRead ? [headerBytes, responseBytes, request.bytes]
      : [headerBytes, request.bytes, responseBytes];
    const permissions = scsiRead ? [['NEXT'], ['NEXT', 'WRITE'], ['WRITE']]
      : [['NEXT'], isRead ? ['NEXT', 'WRITE'] : ['NEXT'], ['WRITE']];
    const usedLength = request.usedLength ?? responseBytes + (isRead ? request.bytes : 0);
    // Implementations can report different reviewed completion accounting. Metadata is explicit.
    requireThat(Number.isSafeInteger(usedLength) && usedLength >= 0
      && usedLength <= lengths.reduce((sum, value) => sum + value, 0), 'request used length');
    return {lengths, permissions, usedLength};
  }

  function validateState(state, request) {
    record(state, 'state');
    requireThat(Object.keys(state).length === STATE_KEYS.length
      && Object.keys(state).every(key => STATE_KEYS.includes(key)), 'state keys');
    FLAGS.forEach(key => requireThat(typeof state[key] === 'boolean', key));
    COUNTERS.forEach(key => requireThat(Number.isInteger(state[key]) && state[key] >= 0, key));
    for (const [flag, dependency] of Object.entries(PRECONDITIONS)) {
      requireThat(!state[flag] || state[dependency], `${flag} requires ${dependency}`);
    }
    requireThat(state.availIndex === Number(state.published), 'available index');
    requireThat(state.backendIndex === Number(state.consumed), 'backend index');
    requireThat(state.usedIndex === Number(state.usedPublished), 'used index');
    requireThat(state.driverIndex === Number(state.reclaimed), 'driver index');
    requireThat(state.freeDescriptors === request.queueSize
      - (state.prepared && !state.reclaimed ? 3 : 0), 'descriptor ownership');
    requireThat(state.statusByte === (state.statusWritten ? '0x00' : '0xff'), 'status byte');
    requireThat(state.usedId === (state.usedWritten ? request.head : null), 'used identity');
    const {usedLength} = requestLayout(request);
    requireThat(state.usedLength === (state.usedWritten ? usedLength : null), 'used length');
    if (request.operation === 'write') {
      requireThat(!state.syscallEntered || state.bufferReady, 'WRITE requires prepared payload');
    } else {
      requireThat(state.bufferReady === state.dataReady, 'READ payload becomes ready with backend result');
    }
  }

  function validateScenario(scenario) {
    record(scenario, 'scenario');
    id(scenario.id, 'scenario id');
    text(scenario.title, 'title');
    text(scenario.summary, 'summary');
    requireThat(Array.isArray(scenario.assumptions) && scenario.assumptions.length > 0, 'assumptions');
    scenario.assumptions.forEach(value => text(value, 'assumption'));

    const baselines = new Map();
    requireThat(Array.isArray(scenario.baselines) && scenario.baselines.length >= 2
      && scenario.baselines.length <= Object.keys(REPOSITORIES).length, 'baselines');
    scenario.baselines.forEach(baseline => {
      record(baseline, 'baseline');
      requireThat(Object.hasOwn(REPOSITORIES, baseline.project), 'baseline project');
      requireThat(!baselines.has(baseline.project), 'duplicate baseline');
      requireThat(/^[0-9a-f]{40}$/.test(baseline.commit), 'baseline commit');
      text(baseline.version, 'baseline version');
      requireThat(baseline.url === `https://github.com/${REPOSITORIES[baseline.project]}/tree/${baseline.commit}`,
        'baseline URL');
      baselines.set(baseline.project, baseline);
    });
    requireThat(baselines.has('Linux'), 'Linux baseline required');

    const request = record(scenario.request, 'request');
    requireThat(['read', 'write'].includes(request.operation), 'request operation');
    requireThat(scenario.id.endsWith(`-${request.operation}`), 'scenario operation identity');
    if (Object.hasOwn(request, 'backend')) id(request.backend, 'request backend');
    if (Object.hasOwn(request, 'command')) text(request.command, 'request command');
    requireThat(request.id === 'R1' && request.bytes === 4096 && request.sector === 2048
      && request.head === 0 && request.queueSize === 8, 'fixed request');
    requireThat(Array.isArray(request.descriptors) && request.descriptors.length === 3, 'descriptor chain');
    const {lengths, permissions} = requestLayout(request);
    const ranges = [];
    request.descriptors.forEach((descriptor, index) => {
      record(descriptor, 'descriptor');
      requireThat(descriptor.id === index && descriptor.len === lengths[index]
        && descriptor.next === (index < 2 ? index + 1 : null), 'direct chain identity');
      requireThat(Array.isArray(descriptor.flags)
        && JSON.stringify(descriptor.flags) === JSON.stringify(permissions[index]), 'descriptor permissions');
      requireThat(typeof descriptor.addr === 'string' && /^0x[0-9a-f]+$/.test(descriptor.addr), 'descriptor address');
      const start = Number(descriptor.addr), end = start + descriptor.len;
      requireThat(Number.isSafeInteger(start) && Number.isSafeInteger(end), 'address range');
      requireThat(!ranges.some(([low, high]) => start < high && end > low), 'overlapping buffers');
      ranges.push([start, end]);
      text(descriptor.role, 'descriptor role');
    });

    requireThat(Array.isArray(scenario.lanes) && scenario.lanes.length >= 5
      && scenario.lanes.length <= LANES.length, 'lanes');
    uniqueList(scenario.lanes.map(lane => lane.id), 'lane ids');
    const laneIds = new Set(scenario.lanes.map(lane => lane.id));
    requireThat(['guest-user', 'guest', 'kvm', 'memory'].every(lane => laneIds.has(lane)), 'required lanes');
    scenario.lanes.forEach(lane => {
      requireThat(LANES.includes(lane.id), 'lane id');
      text(lane.title, 'lane title');
      text(lane.subtitle, 'lane subtitle');
    });
    requireThat(Array.isArray(scenario.phases) && scenario.phases.length === 10, 'phases');
    const phases = scenario.phases.map(phase => {
      id(phase.id, 'phase id'); text(phase.title, 'phase title'); text(phase.summary, 'phase summary');
      return phase.id;
    });
    uniqueList(phases, 'phase ids');

    const sources = record(scenario.sources, 'sources');
    requireThat(Object.keys(sources).length > 0, 'sources');
    for (const [key, source] of Object.entries(sources)) {
      id(key, 'source id'); record(source, 'source');
      const baseline = baselines.get(source.project);
      requireThat(Boolean(baseline), 'source project');
      requireThat(typeof source.path === 'string' && /^[A-Za-z0-9_./-]+$/.test(source.path)
        && !source.path.startsWith('/') && !source.path.split('/').some(part => !part || part === '..' || part === '.'),
      'source path');
      text(source.symbol, 'source symbol');
      requireThat(Number.isSafeInteger(source.line) && source.line > 0, 'source line');
      requireThat(source.url === `https://github.com/${REPOSITORIES[source.project]}/blob/${baseline.commit}/${source.path}#L${source.line}`,
        'source URL must pin baseline and line');
    }

    requireThat(Array.isArray(scenario.callPaths) && scenario.callPaths.length > 0, 'call paths');
    uniqueList(scenario.callPaths.map(path => path.id), 'call path ids');
    scenario.callPaths.forEach(path => {
      record(path, 'call path'); id(path.id, 'call path id');
      requireThat(Object.keys(path).length === 5
        && Object.keys(path).every(key => ['id', 'title', 'context', 'sourceIds', 'boundary'].includes(key)),
      'call path keys');
      ['title', 'context', 'boundary'].forEach(key => text(path[key], `call path ${key}`));
      uniqueList(path.sourceIds, 'call path source references');
      requireThat(path.sourceIds.every(key => Object.hasOwn(sources, key)), 'unknown call path source');
    });

    let state = {...record(scenario.initial, 'initial')};
    validateState(state, request);
    requireThat(FLAGS.every(key => state[key] === false), 'initial flags');
    requireThat(Array.isArray(scenario.steps) && scenario.steps.length >= 2, 'steps');
    uniqueList(scenario.steps.map(step => step.id), 'step ids');
    let previousPhase = 0;
    const visitedPhases = new Set();
    scenario.steps.forEach((step, index) => {
      record(step, 'step'); id(step.id, 'step id');
      ['title', 'description', 'detail'].forEach(key => text(step[key], `step ${key}`));
      requireThat(laneIds.has(step.actor), 'step actor');
      const phase = phases.indexOf(step.phase);
      requireThat(phase >= previousPhase && phase <= previousPhase + 1, 'phase order');
      if (index === 0) requireThat(phase === 0, 'initial phase');
      previousPhase = phase; visitedPhases.add(step.phase);
      uniqueList(step.sourceIds, 'source references');
      requireThat(step.sourceIds.every(key => Object.hasOwn(sources, key)), 'unknown source reference');
      uniqueList(step.active, 'active nodes');
      requireThat(step.active.every(key => NODES.includes(key)), 'unknown active node');
      requireThat(Object.hasOwn(step, 'edge'), 'edge must be explicit or null');
      if (step.edge !== null) {
        const edge = record(step.edge, 'edge');
        requireThat(Object.keys(edge).length === 4
          && Object.keys(edge).every(key => ['from', 'to', 'kind', 'label'].includes(key)), 'edge keys');
        requireThat(NODES.includes(edge.from) && NODES.includes(edge.to)
          && edge.from !== edge.to, 'edge endpoints');
        requireThat(['data', 'notification', 'metadata'].includes(edge.kind), 'edge kind');
        text(edge.label, 'edge label');
      }
      const patch = record(step.changes, 'changes');
      requireThat(Object.keys(patch).every(key => STATE_KEYS.includes(key)), 'unknown state change');
      if (index === 0) requireThat(Object.keys(patch).length === 0, 'initial step must not advance state');
      const next = {...state, ...patch};
      validateState(next, request);
      FLAGS.forEach(key => requireThat(!state[key] || next[key], `non-monotonic ${key}`));
      for (const [flag, dependency] of Object.entries(PRECONDITIONS)) {
        if (!state[flag] && next[flag]) {
          requireThat(state[dependency], `${dependency} must precede ${flag}`);
        }
      }
      if (request.operation === 'write' && !state.syscallEntered && next.syscallEntered) {
        requireThat(state.bufferReady, 'WRITE payload must precede syscall');
      }
      state = next;
    });
    requireThat(visitedPhases.size === phases.length, 'unvisited phase');
    requireThat(state.userReturned, 'request must finish and return to application');
    return true;
  }

  // Reviewed operation changes are declarative. No text replacement or mutation of the base.
  function getScenario(base, operation = 'read', backend = 'local-blk', catalog) {
    requireThat(['read', 'write'].includes(operation), 'requested operation');
    id(backend, 'requested backend');
    validateScenario(base);
    requireThat(base.request.operation === 'read', 'base must be READ');
    const copy = value => JSON.parse(JSON.stringify(value));
    if (backend !== 'local-blk') {
      record(catalog, 'backend catalog');
      requireThat(Array.isArray(catalog.routes) && catalog.routes.length > 0, 'catalog routes');
      uniqueList(catalog.routes.map(route => record(route, 'catalog route').id), 'catalog route ids');
      catalog.routes.forEach(route => {
        id(route.id, 'catalog route id'); text(route.label, 'catalog route label');
        record(route.scenarios, 'catalog scenarios');
      });
      const route = catalog.routes.find(item => item.id === backend);
      requireThat(Boolean(route), 'unknown requested backend');
      requireThat(Object.hasOwn(route.scenarios, operation), 'missing backend operation');
      const selected = record(route.scenarios[operation], 'backend scenario');
      requireThat(selected.request?.backend === backend
        && selected.request?.operation === operation, 'catalog scenario identity');
      requireThat(Object.hasOwn(selected.request, 'interface'), 'catalog request interface');
      validateScenario(selected);
      return copy(selected);
    }
    const scenario = copy(base);
    if (operation === 'read') return scenario;

    const variant = record(record(base.operationVariants, 'operation variants').write, 'WRITE variant');
    const variantKeys = ['title', 'summary', 'assumptions', 'descriptors', 'laneSubtitles',
      'phasePatches', 'sourceMap', 'callPathPatches', 'insertSteps', 'stepPatches'];
    requireThat(Object.keys(variant).length === variantKeys.length
      && Object.keys(variant).every(key => variantKeys.includes(key)), 'WRITE variant keys');
    const apply = (target, patch, keys, label) => {
      record(patch, label);
      requireThat(Object.keys(patch).every(key => keys.includes(key)), `${label} keys`);
      Object.entries(patch).forEach(([key, value]) => {target[key] = copy(value);});
    };
    scenario.id = 'virtio-blk-write';
    scenario.title = variant.title;
    scenario.summary = variant.summary;
    scenario.assumptions = copy(variant.assumptions);
    scenario.request.operation = 'write';
    if (Object.hasOwn(scenario.request, 'command')) scenario.request.command = 'OUT';
    if (Object.hasOwn(scenario.request, 'usedLength')) scenario.request.usedLength = 1;
    scenario.request.descriptors = copy(variant.descriptors);
    for (const [laneId, subtitle] of Object.entries(record(variant.laneSubtitles, 'variant lanes'))) {
      const lane = scenario.lanes.find(item => item.id === laneId);
      requireThat(Boolean(lane), 'unknown variant lane');
      lane.subtitle = subtitle;
    }
    for (const [phaseId, patch] of Object.entries(record(variant.phasePatches, 'variant phases'))) {
      const phase = scenario.phases.find(item => item.id === phaseId);
      requireThat(Boolean(phase), 'unknown variant phase');
      apply(phase, patch, ['title', 'summary'], 'phase patch');
    }
    const sourceMap = record(variant.sourceMap, 'variant source map');
    for (const [from, to] of Object.entries(sourceMap)) {
      requireThat(Object.hasOwn(scenario.sources, from) && Object.hasOwn(scenario.sources, to),
        'unknown variant source');
    }
    [...scenario.steps, ...scenario.callPaths].forEach(item => {
      item.sourceIds = item.sourceIds.map(key => Object.hasOwn(sourceMap, key) ? sourceMap[key] : key);
    });
    for (const [pathId, patch] of Object.entries(record(variant.callPathPatches, 'variant call paths'))) {
      const path = scenario.callPaths.find(item => item.id === pathId);
      requireThat(Boolean(path), 'unknown variant call path');
      apply(path, patch, ['title', 'context', 'sourceIds', 'boundary'], 'call path patch');
    }
    for (const [stepId, patch] of Object.entries(record(variant.stepPatches, 'variant steps'))) {
      const step = scenario.steps.find(item => item.id === stepId);
      requireThat(Boolean(step), 'unknown variant step');
      apply(step, patch, ['title', 'actor', 'description', 'detail', 'sourceIds', 'active', 'edge', 'changes'],
        'step patch');
    }
    requireThat(Array.isArray(variant.insertSteps), 'variant insert steps');
    variant.insertSteps.forEach(insertion => {
      record(insertion, 'step insertion');
      requireThat(Object.keys(insertion).length === 2
        && Object.keys(insertion).every(key => ['after', 'step'].includes(key)), 'step insertion keys');
      const index = scenario.steps.findIndex(item => item.id === insertion.after);
      requireThat(index >= 0, 'unknown insertion anchor');
      scenario.steps.splice(index + 1, 0, copy(insertion.step));
    });
    delete scenario.operationVariants;
    validateScenario(scenario);
    return scenario;
  }

  function buildTrace(scenario) {
    validateScenario(scenario);
    let state = {...scenario.initial};
    return scenario.steps.map((step, index) => {
      state = {...state, ...step.changes};
      return {...step, index, active: [...step.active], sourceIds: [...step.sourceIds],
        edge: step.edge === null ? null : {...step.edge},
        changes: {...step.changes}, state: {...state}};
    });
  }

  // A queue view is a projection of the complete causal trace. Skipped syscall,
  // backend and interrupt work still contributes to each retained snapshot.
  function buildQueueTrace(scenario) {
    const trace = buildTrace(scenario);
    const request = scenario.request;
    const writing = request.operation === 'write';
    const scsi = request.interface === 'virtio-scsi';
    const polling = scenario.presentation?.notificationMode === 'poll';
    const data = request.descriptors.find(descriptor => descriptor.len === request.bytes);
    const response = request.descriptors[scsi && !writing ? 1 : 2];
    const head = request.head;
    const layout = requestLayout(request);
    const steps = [
      {
        flag: 'prepared', title: '准备描述符',
        description: 'R1 与缓冲区已准备。驱动填写直接描述符链 0 → 1 → 2，占用三个描述符。',
        detail: `desc[${data.id}] 指向 ${request.bytes} B 数据区，desc[${response.id}] 指向响应区。`
          + (writing ? '设备读取命令与待写数据，仅响应区带 WRITE 标志。'
            : '设备读取命令，数据区和响应区带 WRITE 标志。')
          + 'WRITE 表示设备对 Guest 内存的写权限；此时设备还不能消费这条链。',
      },
      {
        flag: 'published', title: '发布请求',
        description: `将链头 ${head} 写入 avail.ring[0]，执行发布屏障，再把 avail.idx 从 0 推进到 1。`,
        detail: '描述符和 available 项先准备好，再发布新索引。available 只保存链头编号，'
          + '命令与数据仍在描述符指向的 Guest 内存中；驱动须保持这些缓冲区有效。',
      },
      {
        flag: 'consumed', title: '设备取链',
        description: `设备发现新的 avail.idx，读取链头 ${head} 并沿 next 取出描述符，消费位置从 0 推进到 1。`,
        detail: (polling ? '本场景由 SPDK 轮询共享队列，不发送提交通知。'
          : '本场景检查通知条件后发送 kick，唤醒后端检查队列；通知只提示有工作。')
          + '取走 available 项后，请求仍在处理中，三个描述符仍被占用。',
      },
      {
        flag: 'dataReady', title: '读写缓冲区',
        description: writing
          ? `后端读取 desc[${data.id}] 指向的 ${request.bytes} B 数据并完成本次 I/O，响应和 used 尚未发布。`
          : `后端把 ${request.bytes} B 读取结果写入 desc[${data.id}] 指向的缓冲区，响应和 used 尚未发布。`,
        detail: '数据通过描述符指向的缓冲区交接，available 与 used 保存请求及完成元数据。'
          + (writing ? '本例没有执行 flush 或 FUA，普通写入完成不保证掉电后的持久性。'
            : '数据已经写入用户页，驱动仍需取得完成记录，应用调用随后才能返回。'),
      },
      {
        flag: 'statusWritten', title: '写回响应',
        description: scsi
          ? `设备写入 desc[${response.id}] 的 SCSI 响应：response=OK、status=GOOD、resid=0、sense_len=0。`
          : `设备向 desc[${response.id}] 的状态字节写入 VIRTIO_BLK_S_OK（0）。`,
        detail: '这一步写回请求的处理结果，used 完成项尚未填写，used.idx 仍为 0。'
          + '驱动不能仅凭缓冲区内容变化就回收请求。',
      },
      {
        flag: 'usedWritten', title: '填写完成项',
        description: `设备写入 used.ring[0] = { id: ${head}, len: ${layout.usedLength} }，used.idx 暂不改变。`,
        detail: `used.id 保存原描述符链头 ${head}，驱动用它找回请求。`
          + (request.usedLengthNote || (writing
            ? `used.len=${layout.usedLength}，本场景仅回写响应；待写数据由设备读取。`
            : `used.len=${layout.usedLength}，包含读取数据与响应的完成长度。`))
          + '记录已填写但尚未发布，驱动仍需等待新索引。',
      },
      {
        flag: 'usedPublished', title: '发布完成',
        description: '设备执行写屏障，再把 used.idx 从 0 推进到 1，向驱动发布完成记录。',
        detail: '数据、响应和 used 项先于新索引可见。驱动现在可以按协议检查完成；'
          + '是否发送完成通知取决于通知条件，本场景随后发送通知。',
      },
      {
        flag: 'reclaimed', title: '回收描述符',
        description: `驱动发现新的 used.idx，经过读屏障后取出 used 项，以 id=${head} 找回原链并归还三个描述符。`,
        detail: '驱动消费位置推进到 1，可用描述符恢复为 8。旧的 available 与 used 槽内容可以保留，'
          + '消费位置避免重复处理；队列回收完成后，块层收尾与应用返回继续由完整流程展示。',
      },
    ];
    let previousIndex = -1;
    return steps.map((step, queueIndex) => {
      // Match state transitions, not implementation-specific function/step IDs:
      // SPDK, for example, observes data completion in its NVMe completion poll.
      const frame = trace.find(item => item.changes[step.flag] === true);
      requireThat(Boolean(frame) && frame.index > previousIndex, 'queue milestone order');
      previousIndex = frame.index;
      return {...frame, queueIndex, queueTitle: step.title,
        queueDescription: step.description, queueDetail: step.detail};
    });
  }

  function buildQueueInitial(scenario) {
    const trace = buildTrace(scenario);
    const prepared = trace.findIndex(frame => frame.changes.prepared === true);
    requireThat(prepared > 0, 'queue preparation requires an initial snapshot');
    return {...trace[prepared - 1], changes: {}, queueIndex: -1, queueTitle: '准备前',
      queueDescription: '请求与缓冲区已就绪，描述符尚未填写，队列索引均为 0。',
      queueDetail: '点击下一步准备描述符。浅色表示当前操作，已占用但本步未操作的结构保持中性；旧记录在回收后淡化显示。'};
  }

  return {validateScenario, buildTrace, buildQueueTrace, buildQueueInitial, getScenario};
});
