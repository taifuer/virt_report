/* One RAMBlock, one vCPU and one page stream: a deterministic postcopy model. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-postcopy-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const PAGE_SIZE = 4096, HOST_BASE = 0x10000000, BLOCK = 'pc.ram';
  const stages = ['precopy', 'discard', 'listening', 'active'];
  function createState() {
    return {
      phase: 'precopy', tick: 0, source: [1, 1, 2, 1, 1, 3, 1, 1],
      destination: [1, 1, 1, null, 1, 1, null, null],
      pending: [false, false, true, true, false, true, true, true],
      sent: Array(8).fill(0), requested: Array(8).fill(false), packet: null, request: null, waiting: null,
      lastFault: null, lastRequest: null, lastBlock: null, faults: 0, transfers: 0,
      installed: 0, rejected: false, status: '预拷贝结束前：P2、P5 的目的端副本已过期。点击“下一步”停源并丢弃待传范围。',
      log: ['P0、P1、P4 已有效接收；P2、P5 已再次变脏；P3、P6、P7 从未发送。']
    };
  }
  function record(state, message, rejected = false) {
    state.status = message; state.rejected = rejected;
    state.log = [`${state.tick} · ${message}`, ...state.log].slice(0, 4);
    return state;
  }
  function ready(state) { return state.phase === 'active'; }
  function complete(state) { return ready(state) && state.destination.every(value => value !== null) && !state.packet && !state.request; }
  function beginPacket(state, page, reason) {
    if (!state.pending[page]) return record(state, `P${page} 已安排过传输，不重复发送旧副本。`, true);
    state.pending[page] = false; state.sent[page]++; state.transfers++;
    state.packet = {page, value: state.source[page], stage: 'transit', reason};
    return record(state, `${reason === 'requested' ? '优先响应请求' : '后台扫描'}：发送 P${page} 的 v${state.source[page]}，清除源端待发送位。目的端尚未安装。`);
  }
  function background(state) {
    if (state.packet) return record(state, `单条页面通道正在处理 P${state.packet.page}；请先推进当前传输。`, true);
    if (state.request && state.request.stage === 'queued') {
      const page = state.request.page; state.request = null;
      if (state.pending[page]) return beginPacket(state, page, 'requested');
      return record(state, `请求 P${page} 已被先前传输覆盖，源端跳过重复发送。`);
    }
    const page = state.pending.findIndex(Boolean);
    if (page < 0) return record(state, complete(state) ? '全部 8 页已可用，源端供页任务完成。' : '没有尚未安排的页面；仍需等待在途页安装或请求交接。');
    return beginPacket(state, page, 'background');
  }
  function advancePacket(state) {
    const packet = state.packet, page = packet.page;
    if (packet.stage === 'transit') {
      packet.stage = 'buffered';
      return record(state, `P${page} 的 4096 B 已收齐，位于接收缓冲；尚未装入客户机映射，访问仍需等待。`);
    }
    state.packet = null;
    // Repeated delivery must never replace a page the destination may have modified.
    if (state.destination[page] !== null) return record(state, `P${page} 已存在，拒绝用源端旧副本覆盖目的端内容。`, true);
    state.destination[page] = packet.value; state.installed++; state.requested[page] = false;
    let suffix = '目的端接收位设为 1。';
    if (state.waiting && state.waiting.page === page) {
      const write = state.waiting.write; state.waiting = null;
      if (write) state.destination[page]++;
      suffix = `等待的 vCPU 被唤醒，${write ? `写入完成，目的端变为 v${state.destination[page]}` : `读取到 v${state.destination[page]}`}；接收位设为 1。`;
    }
    if (state.request && state.request.page === page && state.request.stage === 'fault') state.request = null;
    return record(state, `UFFDIO_COPY 原子安装 P${page}。${suffix}`);
  }
  function advance(state) {
    if (state.phase === 'precopy') {
      state.phase = 'discard';
      state.destination = state.destination.map((value, page) => state.pending[page] ? null : value);
      return record(state, '源端停止执行并完成最终脏页同步。DISCARD 移除 P2、P3、P5、P6、P7 的目的端映射，清除对应接收位。');
    }
    if (state.phase === 'discard') {
      state.phase = 'listening';
      return record(state, '目的端处理 LISTEN：userfaultfd 与页面接收路径就绪，随后恢复包中的 CPU 和设备状态；vCPU 尚未启动。');
    }
    if (state.phase === 'listening') {
      state.phase = 'active';
      return record(state, '目的端处理 RUN 并开始执行；源端保持暂停。选择 P2 读写可触发缺页，或先让后台发送。');
    }
    if (state.request && state.request.stage === 'fault') {
      const request = state.request;
      if (state.destination[request.page] !== null) {
        state.request = null; return record(state, '缺页线程检查到页面已安装，省略返回通道请求。');
      }
      request.stage = 'return-path'; state.requested[request.page] = true;
      const withId = state.lastBlock !== BLOCK;
      state.lastBlock = BLOCK;
      state.lastRequest = {
        page: request.page, type: withId ? 'MIG_RP_MSG_REQ_PAGES_ID' : 'MIG_RP_MSG_REQ_PAGES',
        start: request.page * PAGE_SIZE, length: PAGE_SIZE, id: withId ? BLOCK : null,
        payloadLength: withId ? 12 + 1 + BLOCK.length : 12
      };
      return record(state, `缺页线程将宿主地址换为 ${BLOCK} + 0x${(request.page * PAGE_SIZE).toString(16)}；经返回通道请求 4096 B。`);
    }
    if (state.request && state.request.stage === 'return-path') {
      state.request.stage = 'queued';
      return record(state, `源端返回路径线程收到 P${state.request.page} 请求并入队，等待 RAM 发送路径处理。`);
    }
    if (state.request && state.request.stage === 'queued' && !state.pending[state.request.page]) {
      const page = state.request.page; state.request = null;
      return record(state, `源端检查 bmap[P${page}] = 0：后台已经安排过该页，跳过重复请求，现有传输继续。`);
    }
    if (state.packet) return advancePacket(state);
    return background(state);
  }
  function apply(input, action) {
    if (!action || !['advance', 'access', 'write', 'background'].includes(action.kind)) throw new RangeError('unknown postcopy action');
    let page = null;
    if (action.kind === 'access' || action.kind === 'write') {
      page = Number(action.page);
      if (action.page === null || action.page === '' || !Number.isInteger(page) || page < 0 || page >= 8) throw new RangeError('page must be between 0 and 7');
    }
    const state = JSON.parse(JSON.stringify(input)); state.tick++; state.rejected = false;
    if (action.kind === 'advance') return advance(state);
    if (!ready(state)) return record(state, '目的端 vCPU 尚未恢复；请先按“下一步”完成 DISCARD、LISTEN 和 RUN。', true);
    if (action.kind === 'background') return background(state);
    if (state.waiting) return record(state, `这个 vCPU 正等待 P${state.waiting.page}，不能先执行另一条读写；可继续推进供页过程。`, true);
    const write = action.kind === 'write';
    if (state.destination[page] !== null) {
      if (write) state.destination[page]++;
      return record(state, write ? `目的端写 P${page}，内容变为 v${state.destination[page]}；源端旧副本保持 v${state.source[page]}。` : `P${page} 已可访问，直接读取 v${state.destination[page]}，不发送远程请求。`);
    }
    state.faults++; state.waiting = {page, write}; state.request = {page, write, stage: 'fault'};
    state.lastFault = {page, event: 'UFFD_EVENT_PAGEFAULT', address: HOST_BASE + page * PAGE_SIZE + 32, flags: write ? 1 : 0, offset: page * PAGE_SIZE};
    return record(state, `目的端${write ? '写' : '读'} P${page} 触发 missing 缺页；内核记录事件，vCPU 等待。客户机映射还没有页内容。`);
  }
  const example = [
    {kind: 'advance'}, {kind: 'advance'}, {kind: 'advance'},
    {kind: 'write', page: 2}, {kind: 'advance'}, {kind: 'advance'},
    {kind: 'advance'}, {kind: 'advance'}, {kind: 'advance'},
    {kind: 'access', page: 2}, {kind: 'background'}, {kind: 'access', page: 3},
    {kind: 'advance'}, {kind: 'advance'}, {kind: 'advance'},
    {kind: 'advance'}, {kind: 'advance'},
    ...Array.from({length: 9}, () => ({kind: 'advance'}))
  ];
  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-postcopy-${name}]`), pageControl = get('page');
    let state = createState(), demoIndex = null;
    const make = (tag, className, text) => {
      const node = doc.createElement(tag); if (className) node.className = className;
      if (text !== undefined) node.textContent = text; return node;
    };
    const hex = value => `0x${value.toString(16)}`;
    function pages(name, values) {
      const node = get(name); node.replaceChildren();
      values.forEach((value, page) => {
        const cell = make('div', 'postcopy-page');
        const stale = name === 'destination' && state.phase === 'precopy' && value !== null && value !== state.source[page];
        cell.dataset.state = value === null ? 'missing' : stale ? 'stale' : 'present';
        cell.dataset.selected = String(page === Number(pageControl.value));
        cell.append(make('small', '', `P${page}`), make('strong', '', value === null ? '缺失' : `v${value}`));
        cell.append(make('small', '', stale ? '过期' : state.waiting && state.waiting.page === page && name === 'destination' ? '访问等待' : value === null ? state.packet && state.packet.page === page ? '接收中' : '待交付' : name === 'source' ? state.pending[page] ? '待发送' : '保留副本' : '可访问'));
        node.append(cell);
      });
    }
    function bitmap(name, bits) {
      const node = get(name); node.replaceChildren();
      bits.forEach((value, page) => {
        const cell = make('span', 'postcopy-bit'); cell.dataset.set = String(Boolean(value));
        cell.append(make('small', '', `P${page}`), make('strong', '', value ? '1' : '0')); node.append(cell);
      });
    }
    function fields(name, pairs) {
      const node = get(name); node.replaceChildren();
      pairs.forEach(([key, value]) => node.append(make('dt', '', key), make('dd', '', value)));
    }
    function paint() {
      lab.dataset.phase = state.phase; lab.dataset.blocked = String(Boolean(state.waiting)); lab.dataset.rejected = String(state.rejected);
      get('status').textContent = state.status;
      get('source-cpu').textContent = state.phase === 'precopy' ? '源端 vCPU：预拷贝中' : '源端 vCPU：已暂停 · 页内容固定';
      get('destination-cpu').textContent = !ready(state) ? '目的端 vCPU：尚未启动' : state.waiting ? `目的端 vCPU：等待 P${state.waiting.page}` : '目的端 vCPU：可运行';
      pages('source', state.source); pages('destination', state.destination);
      bitmap('pending', state.pending); bitmap('received', state.destination.map(value => value !== null));
      const phases = get('phases'); phases.replaceChildren();
      ['预拷贝末段', '停源与 DISCARD', 'LISTEN / 状态恢复', 'RUN / 供页'].forEach((text, index) => {
        const item = make('li', '', text); if (stages[index] === state.phase) item.setAttribute('aria-current', 'step'); phases.append(item);
      });
      const request = state.request, packet = state.packet;
      get('request-path').textContent = request ? `P${request.page} · ${{fault: 'userfaultfd 事件待处理', 'return-path': '请求在返回通道中', queued: '已进入源端请求队列'}[request.stage]}` : '暂无待处理请求';
      get('data-path').textContent = packet ? `P${packet.page} · v${packet.value} · ${packet.reason === 'requested' ? '按需响应' : '后台传输'} · ${packet.stage === 'transit' ? '数据在途' : '已接收完整页'}` : '页面通道空闲';
      get('buffer').textContent = packet && packet.stage === 'buffered' ? `P${packet.page} · 4096 B 已齐 · 等待 UFFDIO_COPY` : '尚无待安装的完整页';
      get('buffer').parentElement.dataset.ready = String(Boolean(packet && packet.stage === 'buffered'));
      const fault = state.lastFault;
      const requestedPages = state.requested.flatMap((value, page) => value ? [`P${page}`] : []);
      fields('fault-record', fault ? [['event', fault.event], ['address', hex(fault.address)], ['flags', fault.flags ? '0x1 · WRITE' : '0x0 · 读访问'], ['定位', `${BLOCK} + ${hex(fault.offset)}（按 4096 B 对齐）`], ['page_requested', requestedPages.length ? `${requestedPages.join('、')} 等待解决` : '空（缺页线程登记后加入）']] : [['event', '尚无缺页事件'], ['RAMBlock', BLOCK], ['host / page_size', `${hex(HOST_BASE)} / 4096 B`]]);
      const last = state.lastRequest;
      fields('request-record', last ? [['type', last.type], ['start · BE64', hex(last.start)], ['len · BE32', `${last.length} B`], ['id', last.id || `省略，复用 ${BLOCK}`], ['payload_length', `${last.payloadLength} B`]] : [['type', '尚未发送页面请求'], ['方向', '目的端 → 源端'], ['携带内容', '块名、块内偏移和长度，不携带页数据']]);
      get('counters').textContent = `逻辑步 ${state.tick} · 缺页 ${state.faults} 次 · 后拷贝发送 ${state.transfers} 页 · 已安装 ${state.destination.filter(value => value !== null).length} / 8 页${complete(state) ? ' · 内存交付完成' : ''}`;
      const events = get('events'); events.replaceChildren(); state.log.forEach(line => events.append(make('li', '', line)));
      get('demo').textContent = demoIndex === null ? '示例下一步' : demoIndex >= example.length ? '重新演示' : `示例下一步 · ${demoIndex}/${example.length}`;
      get('next').textContent = state.phase === 'precopy' ? '下一步 · 停源与丢弃' : state.phase === 'discard' ? '下一步 · 接收就绪' : state.phase === 'listening' ? '下一步 · 恢复运行' : '下一步';
    }
    function dispatch(kind) { demoIndex = null; state = apply(state, {kind, page: pageControl.value}); paint(); }
    function reset() { state = createState(); demoIndex = null; paint(); }
    ['access', 'write', 'background'].forEach(kind => get(kind).addEventListener('click', () => dispatch(kind)));
    get('next').addEventListener('click', () => dispatch('advance'));
    get('reset').addEventListener('click', reset); pageControl.addEventListener('change', paint);
    get('demo').addEventListener('click', () => {
      if (demoIndex === null || demoIndex >= example.length) { state = createState(); demoIndex = 0; }
      const action = example[demoIndex++]; if (action.page !== undefined) pageControl.value = String(action.page);
      state = apply(state, action); paint();
    });
    get('controls').hidden = false; get('scene').hidden = false; get('status').hidden = false; get('fallback').hidden = true;
    paint(); return {state: () => state, dispatch, reset};
  }
  return {PAGE_SIZE, HOST_BASE, BLOCK, createState, apply, ready, complete, example, mount};
});
