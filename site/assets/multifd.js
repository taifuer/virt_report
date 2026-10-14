/* Deterministic ordering model for the QEMU 10.2 socket MultiFD RAM path. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) {
    root.document.querySelectorAll('[data-multifd-lab]').forEach(lab => api.mount(lab, root));
  }
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  function normalizeConfig(config = {}) {
    const channels = Number(config.channels ?? 4), delay = Number(config.delay ?? 3);
    if (![2, 4, 8].includes(channels)) throw new RangeError('channels must be 2, 4 or 8');
    if (![0, 3, 6].includes(delay)) throw new RangeError('delay must be 0, 3 or 6');
    return {channels, delay};
  }
  const pageName = page => `P${String(page).padStart(2, '0')}`;
  function batches(epoch) {
    const pages = epoch === 1 ? Array.from({length: 16}, (_, i) => i) : [0, 1, 4, 5, 8, 9, 12, 13];
    const result = [];
    for (let i = 0; i < pages.length; i += 2) {
      result.push({id: `${epoch}.${i / 2 + 1}`, pages: pages.slice(i, i + 2), version: epoch});
    }
    return result;
  }
  function createState(config) {
    const options = normalizeConfig(config);
    return {
      config: options, tick: 0, epoch: 1, phase: 'ready', nextLane: 0,
      pending: batches(1), assigned: 0, delivered: 0, totalDelivered: 0,
      source: Array(16).fill(1), destination: Array(16).fill(0),
      localDone: false, mainFlight: null, mainArrived: false,
      lanes: Array.from({length: options.channels}, (_, id) => ({
        id, job: null, queue: [], syncArrived: false, received: 0, latest: '尚未接收'
      })),
      log: ['尚未开始：两组 RAM 页传输，目的端不运行客户机。']
    };
  }
  function addLog(state, value) {
    state.log.push(`第 ${state.tick} 步 · ${value}`);
    state.log = state.log.slice(-4);
  }
  function step(previous) {
    if (previous.phase === 'done') return previous;
    const state = JSON.parse(JSON.stringify(previous));
    state.tick++;
    if (state.phase === 'barrier') {
      if (state.epoch === 2) {
        state.phase = 'done';
        addLog(state, '第二组同步完成；本例 RAM 内容一致，CPU / 设备状态仍需后续迁移。');
        return state;
      }
      state.epoch = 2; state.phase = 'transfer'; state.pending = batches(2);
      state.assigned = 0; state.delivered = 0; state.localDone = false;
      state.mainArrived = false; state.mainFlight = null;
      state.lanes.forEach(lane => { lane.syncArrived = false; lane.latest = '已释放，等待下一批'; });
      state.pending.forEach(batch => batch.pages.forEach(page => { state.source[page] = 2; }));
      addLog(state, '源端 8 页更新为 v2；接收线程已释放，开始下一组。');
      return state;
    }
    if (state.phase === 'ready') {
      state.phase = 'transfer';
      addLog(state, '已建立主流与数据通道；开始分发第一组页面。');
    }
    // Receiver progress is independent of local write completion. Each lane is FIFO.
    state.lanes.forEach(lane => {
      if (!lane.queue.length || lane.syncArrived) return;
      const packet = lane.queue[0];
      packet.remaining--;
      if (packet.remaining > 0) return;
      lane.queue.shift();
      if (packet.kind === 'sync') {
        lane.syncArrived = true;
        lane.latest = '已收 SYNC，等待释放';
        addLog(state, `通道 ${lane.id + 1} 收到 SYNC；本通道此前页面已装载。`);
      } else {
        packet.batch.pages.forEach(page => { state.destination[page] = packet.batch.version; });
        lane.received += packet.batch.pages.length;
        state.delivered += packet.batch.pages.length;
        state.totalDelivered += packet.batch.pages.length;
        lane.latest = `${packet.batch.pages.map(pageName).join(' / ')} 已装载`;
        addLog(state, `通道 ${lane.id + 1} 装载批次 ${packet.batch.id}（v${packet.batch.version}）。`);
      }
    });
    if (state.mainFlight !== null) {
      state.mainFlight--;
      if (state.mainFlight === 0) {
        state.mainFlight = null; state.mainArrived = true;
        addLog(state, '主流读到 FLUSH，目的端等待全部通道的 SYNC。');
      }
    }
    state.lanes.forEach(lane => {
      if (!lane.job) return;
      lane.job.remaining--;
      if (lane.job.remaining > 0) return;
      const duration = 2 + (lane.id === 0 ? state.config.delay : 0);
      lane.queue.push({kind: 'data', batch: lane.job.batch, duration, remaining: duration});
      lane.job = null;
    });
    // Round-robin search starts after the previous assignment, skipping busy workers.
    while (state.pending.length) {
      let chosen = null;
      for (let offset = 0; offset < state.lanes.length; offset++) {
        const index = (state.nextLane + offset) % state.lanes.length;
        if (!state.lanes[index].job) { chosen = state.lanes[index]; break; }
      }
      if (!chosen) break;
      const batch = state.pending.shift();
      chosen.job = {batch, remaining: 2}; state.assigned++;
      state.nextLane = (chosen.id + 1) % state.lanes.length;
    }
    if (!state.localDone && !state.pending.length && state.lanes.every(lane => !lane.job)) {
      state.localDone = true; state.phase = 'sync';
      state.lanes.forEach(lane => {
        const duration = 1 + (lane.id === 0 ? state.config.delay : 0);
        lane.queue.push({kind: 'sync', duration, remaining: duration});
      });
      state.mainFlight = 1;
      addLog(state, '发送线程已写完任务和 SYNC；主线程写出 FLUSH，远端仍可能在接收。');
    }
    if (state.mainArrived && state.lanes.every(lane => lane.syncArrived)) {
      state.phase = 'barrier';
      addLog(state, `第 ${state.epoch} 组全部通道到达屏障，目的端可释放接收线程。`);
    }
    return state;
  }
  function describe(state) {
    const count = state.lanes.filter(lane => lane.syncArrived).length;
    if (state.phase === 'ready') return '准备就绪。点击“下一步”查看任务分发，或播放完整过程。';
    if (state.phase === 'done') return '两组 RAM 传输与同步结束。目的端已装载 v2 更新；本例未执行 CPU / 设备状态恢复。';
    if (state.phase === 'barrier') return `第 ${state.epoch} 组屏障已满足：主流 FLUSH 和 ${count} 条通道的 SYNC 均已到达。`;
    if (state.mainArrived) return `第 ${state.epoch} 组：源端本地写入已完成；目的端主流等待 SYNC（${count} / ${state.lanes.length}）。`;
    if (state.localDone) return `第 ${state.epoch} 组：本地发送线程已完成，主流 FLUSH 正在传输。`;
    return `第 ${state.epoch} 组：已分发 ${state.assigned} / ${batches(state.epoch).length} 批，目的端已装载 ${state.delivered} / ${state.epoch === 1 ? 16 : 8} 页。`;
  }
  function mount(lab, environment) {
    const doc = environment.document;
    const get = name => lab.querySelector(`[data-multifd-${name}]`);
    const controls = get('controls'), channels = get('channels'), delay = get('delay');
    const play = get('play'), next = get('step'), status = get('status');
    const laneContainer = get('lanes'), sourceContainer = get('source-pages');
    const destinationContainer = get('destination-pages');
    let state = createState({channels: channels.value, delay: delay.value});
    let timer = null, playing = false, generation = 0, rows = [], sourcePages = [], destinationPages = [];
    const element = (tag, className, text) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text) node.textContent = text;
      return node;
    };
    function build() {
      laneContainer.replaceChildren();
      rows = state.lanes.map(lane => {
        const row = element('div', 'multifd-lane');
        const sender = element('div', 'multifd-sender');
        sender.append(element('strong', '', `通道 ${lane.id + 1}`));
        const job = element('small'); sender.append(job);
        const track = element('div', 'multifd-track'), packet = element('span');
        track.append(packet);
        const receiver = element('div', 'multifd-receiver');
        const result = element('strong'), queue = element('small'); receiver.append(result, queue);
        row.append(sender, track, receiver); laneContainer.append(row);
        return {row, job, packet, result, queue};
      });
      function buildPages(container) {
        container.replaceChildren();
        return Array.from({length: 16}, (_, index) => {
          const node = element('span', 'multifd-page', pageName(index));
          const version = element('small'); node.append(version); container.append(node);
          return {node, version};
        });
      }
      sourcePages = buildPages(sourceContainer); destinationPages = buildPages(destinationContainer);
    }
    function paint() {
      status.textContent = describe(state);
      lab.dataset.phase = state.phase;
      lab.dataset.epoch = String(state.epoch);
      state.lanes.forEach((lane, index) => {
        const row = rows[index], front = lane.queue[0];
        row.row.dataset.slow = String(index === 0 && state.config.delay > 0);
        row.row.dataset.waiting = String(lane.syncArrived);
        row.job.textContent = lane.job ? `发送批次 ${lane.job.batch.id}` : state.localDone ? 'SYNC 已写出' : '等待任务';
        row.packet.textContent = front ? front.kind === 'sync' ? 'SYNC' : `批 ${front.batch.id}` : '';
        row.packet.style.left = front ? `${Math.round((1 - front.remaining / front.duration) * 45)}%` : '0%';
        row.result.textContent = lane.latest;
        row.queue.textContent = `已接收 ${lane.received} 页${lane.queue.length ? ` · 队列 ${lane.queue.length}` : ''}`;
      });
      const waiting = state.lanes.filter(lane => lane.syncArrived).length;
      get('main-packet').textContent = state.mainFlight !== null ? 'FLUSH' : '';
      get('main-status').textContent = state.mainArrived ? waiting === state.lanes.length ? '屏障满足' : `等待 SYNC ${waiting}/${state.lanes.length}` : state.localDone ? 'FLUSH 已写出' : '等待同步点';
      get('local').textContent = state.localDone ? '全部本地写入完成' : '处理页面批次';
      get('remote').textContent = state.phase === 'barrier' || state.phase === 'done' ? '本组已汇合，可继续' : `SYNC ${waiting} / ${state.lanes.length}${state.mainArrived ? ' · 主流等待' : ''}`;
      for (let i = 0; i < 16; i++) {
        sourcePages[i].node.dataset.version = String(state.source[i]);
        sourcePages[i].version.textContent = `v${state.source[i]}`;
        destinationPages[i].node.dataset.version = String(state.destination[i]);
        destinationPages[i].version.textContent = state.destination[i] ? `v${state.destination[i]}` : '未到';
      }
      get('counters').textContent = `逻辑步 ${state.tick} · 第 ${state.epoch} / 2 组 · 累计装载 ${state.totalDelivered} / 24 页（含重传）`;
      const events = get('events'); events.replaceChildren();
      state.log.forEach(line => events.append(element('li', '', line)));
      next.disabled = state.phase === 'done'; play.disabled = state.phase === 'done';
    }
    function pause() {
      playing = false; generation++;
      if (timer !== null) environment.clearTimeout(timer);
      timer = null; play.textContent = '播放'; play.setAttribute('aria-pressed', 'false');
    }
    function advance() {
      state = step(state); paint();
      if (state.phase === 'done') pause();
    }
    function schedule() {
      if (!playing) return;
      const current = generation;
      timer = environment.setTimeout(() => {
        if (!playing || current !== generation) return;
        timer = null; advance(); schedule();
      }, 700);
    }
    function reset() {
      pause(); state = createState({channels: channels.value, delay: delay.value}); build(); paint();
    }
    next.addEventListener('click', () => { pause(); advance(); });
    play.addEventListener('click', () => {
      if (playing) { pause(); return; }
      playing = true; play.textContent = '暂停'; play.setAttribute('aria-pressed', 'true'); schedule();
    });
    get('reset').addEventListener('click', reset);
    channels.addEventListener('change', reset); delay.addEventListener('change', reset);
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) pause(); });
    // No coordinate-dependent animations: moving this DOM into the viewer preserves state.
    controls.hidden = false; status.hidden = false; get('scene').hidden = false;
    get('fallback').hidden = true; build(); paint();
    return {state: () => state, advance, reset, pause};
  }
  return {normalizeConfig, createState, step, describe, mount};
});
