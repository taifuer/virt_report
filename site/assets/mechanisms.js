/* Deterministic round-based precopy model. Units are MiB, seconds, and milliseconds. */
(function (root) {
  'use strict';
  const DEFAULT_PARAMETERS = Object.freeze({memoryMiB: 8192, bandwidthMiBps: 1000, dirtyMiBps: 256, downtimeMs: 100});
  const PRESETS = Object.freeze({
    easy: DEFAULT_PARAMETERS,
    slow: Object.freeze({...DEFAULT_PARAMETERS, dirtyMiBps: 800}),
    hard: Object.freeze({...DEFAULT_PARAMETERS, dirtyMiBps: 1200})
  });
  const MAX_ROUNDS = 32;
  const STABLE_ROUNDS = 3;
  const OVERHEAD_MS = Object.freeze({state: 20, restore: 20});
  const LIMITS = Object.freeze({memoryMiB: [64, 1048576], bandwidthMiBps: [1, 1048576], dirtyMiBps: [0, 1048576], downtimeMs: [1, 10000]});
  const LABELS = {memoryMiB: '初始内存', bandwidthMiBps: '有效传输带宽', dirtyMiBps: '去重脏页速率', downtimeMs: '目标停机时间'};

  function validateParameters(parameters = {}) {
    if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) throw new RangeError('参数必须为对象');
    const result = {...DEFAULT_PARAMETERS, ...parameters};
    for (const [name, bounds] of Object.entries(LIMITS)) {
      if (typeof result[name] !== 'number' || !Number.isFinite(result[name]) || result[name] < bounds[0] || result[name] > bounds[1]) {
        throw new RangeError(`${LABELS[name]}应为 ${bounds[0]} 至 ${bounds[1]} 之间的有效数值。`);
      }
    }
    return result;
  }

  function createState(parameters = {}) {
    const values = validateParameters(parameters);
    return {
      parameters: values, phase: 'ready', rounds: 0, step: 0,
      remainingMiB: values.memoryMiB, transferredMiB: 0, elapsedSeconds: 0,
      thresholdMiB: values.bandwidthMiBps * values.downtimeMs / 1000,
      lastTransferMiB: 0, lastDurationSeconds: 0, stableRounds: 0,
      sourceRunning: true, destinationRunning: false, observationEnded: false,
      destinationStateReady: false,
      stopRemainingMiB: null, downtimeMs: null, endReason: null,
      points: [{round: 0, remainingMiB: values.memoryMiB}]
    };
  }

  function finished(state) { return state.phase === 'complete' || state.observationEnded; }

  function estimateDowntime(state) {
    const remaining = state.stopRemainingMiB === null ? state.remainingMiB : state.stopRemainingMiB;
    const ramMs = remaining / state.parameters.bandwidthMiBps * 1000;
    return {ramMs, stateMs: OVERHEAD_MS.state, restoreMs: OVERHEAD_MS.restore, totalMs: ramMs + OVERHEAD_MS.state + OVERHEAD_MS.restore};
  }

  function advance(previous) {
    if (finished(previous)) return previous;
    const state = {...previous, parameters: {...previous.parameters}, points: previous.points.map(point => ({...point})), step: previous.step + 1};
    const {memoryMiB, bandwidthMiBps, dirtyMiBps} = state.parameters;
    if (state.phase === 'paused') {
      const timing = estimateDowntime(state);
      state.phase = 'final_ram';
      state.lastTransferMiB = state.remainingMiB;
      state.lastDurationSeconds = timing.ramMs / 1000;
      state.transferredMiB += state.remainingMiB;
      state.elapsedSeconds += timing.ramMs / 1000;
      state.remainingMiB = 0;
      state.points.push({round: state.rounds + 1, remainingMiB: 0, final: true});
      return state;
    }
    if (state.phase === 'final_ram') {
      state.phase = 'state_transfer';
      state.lastTransferMiB = 0;
      state.lastDurationSeconds = OVERHEAD_MS.state / 1000;
      state.elapsedSeconds += state.lastDurationSeconds;
      state.destinationStateReady = true;
      return state;
    }
    if (state.phase === 'state_transfer') {
      state.phase = 'complete';
      state.lastTransferMiB = 0;
      state.lastDurationSeconds = OVERHEAD_MS.restore / 1000;
      state.elapsedSeconds += state.lastDurationSeconds;
      state.downtimeMs = estimateDowntime(state).totalMs;
      state.destinationRunning = true;
      return state;
    }
    // Complete the first scan before testing this simplified stop threshold.
    if (state.sourceRunning && state.rounds > 0 && state.remainingMiB <= state.thresholdMiB) {
      state.phase = 'paused';
      state.sourceRunning = false;
      state.stopRemainingMiB = state.remainingMiB;
      state.lastTransferMiB = 0;
      state.lastDurationSeconds = 0;
      return state;
    }
    const before = state.remainingMiB;
    state.lastTransferMiB = before;
    state.lastDurationSeconds = before / bandwidthMiBps;
    state.transferredMiB += before;
    state.elapsedSeconds += state.lastDurationSeconds;
    state.remainingMiB = Math.min(memoryMiB, dirtyMiBps * state.lastDurationSeconds);
    state.rounds += 1;
    state.phase = 'precopy';
    state.points.push({round: state.rounds, remainingMiB: state.remainingMiB});
    state.stableRounds = state.remainingMiB >= before - 1e-9 ? state.stableRounds + 1 : 0;
    if (state.remainingMiB > state.thresholdMiB && (state.stableRounds >= STABLE_ROUNDS || state.rounds >= MAX_ROUNDS)) {
      state.observationEnded = true;
      state.endReason = state.stableRounds >= STABLE_ROUNDS ? 'stable' : 'limit';
    }
    return state;
  }

  // Equal-capacity illustrative regions, not guest pages or real RAMBlock boundaries.
  function memoryRegions(state) {
    const count = 12;
    const size = state.parameters.memoryMiB / count;
    return Array.from({length: count}, (_, index) => ({
      label: String.fromCharCode(65 + index),
      dirtyFraction: Math.max(0, Math.min(1, (state.remainingMiB - index * size) / size))
    }));
  }

  const api = {DEFAULT_PARAMETERS, PRESETS, MAX_ROUNDS, STABLE_ROUNDS, OVERHEAD_MS, LIMITS, validateParameters, createState, advance, finished, estimateDowntime, memoryRegions};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  function number(value) {
    return value.toLocaleString('zh-CN', {maximumFractionDigits: value !== 0 && Math.abs(value) < 0.1 ? 3 : 1});
  }
  function milliseconds(value) { return `${value.toLocaleString('zh-CN', {maximumFractionDigits: 3})} ms`; }
  function duration(seconds) { return seconds < 1 ? milliseconds(seconds * 1000) : `${number(seconds)} s`; }
  function initialize(lab) {
    const inputs = [...lab.querySelectorAll('[data-precopy-parameter]')];
    const previous = lab.querySelector('[data-migration-back]');
    const next = lab.querySelector('[data-migration-next]');
    const play = lab.querySelector('[data-migration-play]');
    const reset = lab.querySelector('[data-migration-reset]');
    const status = lab.querySelector('[data-migration-status]');
    const interactive = lab.querySelector('[data-migration-interactive]');
    const error = lab.querySelector('[data-precopy-error]');
    const output = lab.querySelector('[data-precopy-output]');
    if (inputs.length !== 4 || !previous || !next || !play || !reset || !status || !interactive || !error || !output) return;
    let history = [createState()];
    let index = 0;
    let timer = null;
    let invalid = false;
    let playing = false;
    let transition = null;
    let frame = null;
    let lastFrame = null;
    const scene = lab.querySelector('[data-precopy-scene]');
    const flights = lab.querySelector('[data-precopy-flights]');
    const memory = {};
    for (const side of ['source', 'destination']) {
      memory[side] = Array.from({length: 12}, (_, index) => {
        const cell = document.createElement('span');
        cell.className = 'precopy-memory-cell';
        cell.innerHTML = `<b>${String.fromCharCode(65 + index)}</b><span></span>`;
        lab.querySelector(`[data-precopy-${side}-memory]`).append(cell);
        return cell;
      });
    }
    const reducedMotion = root.matchMedia('(prefers-reduced-motion: reduce)');
    function put(name, text) {
      const element = lab.querySelector(`[data-precopy-${name}]`);
      if (element.textContent !== text) element.textContent = text;
    }
    function stop(cancel = false) {
      playing = false;
      if (timer !== null) root.clearTimeout(timer);
      timer = null;
      if (frame !== null) root.cancelAnimationFrame(frame);
      frame = null; lastFrame = null;
      if (cancel) { transition = null; flights.replaceChildren(); }
      status.setAttribute('aria-live', 'polite');
    }
    function drawControls() {
      const busy = transition !== null;
      previous.disabled = invalid || (!busy && index === 0);
      next.disabled = invalid || busy || finished(history[index]);
      play.disabled = invalid || finished(history[index]);
      const active = playing || frame !== null;
      play.textContent = active ? '暂停' : busy ? '继续' : '播放';
      play.setAttribute('aria-pressed', String(active));
    }
    function drawScene(state, movement = null) {
      const after = movement ? movement.after : state;
      const sendingRam = movement && movement.kind === 'ram';
      const sourceStopped = !state.sourceRunning;
      put('source-vm', state.sourceRunning ? '虚拟机运行中' : '虚拟机已暂停');
      put('destination-vm', state.destinationRunning ? '虚拟机运行中' : '虚拟机尚未运行');
      put('write-state', sourceStopped ? '写入已冻结' : sendingRam ? '写入 → 脏页' : '持续写入');
      put('receive-state', sendingRam ? '接收中' : state.phase === 'ready' ? '等待接收' : state.remainingMiB ? '仍有旧副本' : 'RAM 已同步');
      const fromRegions = memoryRegions(state), toRegions = memoryRegions(after);
      for (let i = 0; i < 12; i += 1) {
        const token = movement && movement.tokens.find(item => item.region === i);
        const departed = token && token.progress > 0;
        const dirtied = token && token.progress >= 0.35 && after.sourceRunning;
        const arrived = token && token.progress >= 1;
        let dirty = state.phase === 'ready' ? 0 : fromRegions[i].dirtyFraction;
        if (departed) dirty = dirtied ? toRegions[i].dirtyFraction : 0;
        const source = memory.source[i];
        source.className = `precopy-memory-cell${dirty ? ' is-dirty' : ' is-clean'}${dirtied && dirty ? ' is-writing' : ''}`;
        source.style.setProperty('--dirty-percent', `${dirty * 100}%`);
        source.lastElementChild.textContent = dirty ? '脏' : '新';
        source.title = `聚合区域 ${fromRegions[i].label}：${dirty ? `待重传比例 ${number(dirty * 100)}%` : '当前内容'}`;
        const empty = state.phase === 'ready' && !arrived;
        let stale = state.phase === 'ready' ? 0 : fromRegions[i].dirtyFraction;
        if (arrived) stale = toRegions[i].dirtyFraction;
        else if (dirtied && !empty) stale = Math.max(stale, toRegions[i].dirtyFraction);
        const destination = memory.destination[i];
        destination.className = `precopy-memory-cell${empty ? ' is-empty' : stale ? ' is-dirty' : ' is-clean'}${arrived ? ' is-received' : ''}`;
        destination.style.setProperty('--dirty-percent', `${stale * 100}%`);
        destination.lastElementChild.textContent = empty ? '—' : stale ? '旧' : '新';
        destination.title = `聚合区域 ${fromRegions[i].label}：${empty ? '尚未接收' : stale ? `旧副本比例 ${number(stale * 100)}%` : '已同步副本'}`;
      }
      put('source-copy', sendingRam ? `本轮起始待传 ${number(state.remainingMiB)} MiB` : state.phase === 'ready' ? `待首传 ${number(state.remainingMiB)} MiB` : `待同步 ${number(state.remainingMiB)} MiB`);
      put('destination-copy', sendingRam ? `本轮收到 ${movement.tokens.filter(token => token.progress >= 1).length} / ${movement.tokens.length} 组` : state.phase === 'ready' ? '尚无内存副本' : state.remainingMiB ? `仍差 ${number(state.remainingMiB)} MiB` : '内存副本已同步');
      for (const kind of ['cpu', 'device']) {
        const delivered = state.destinationStateReady || (movement && movement.tokens.some(token => token.kind === kind && token.progress >= 1));
        put(`source-${kind}-text`, state.sourceRunning ? '运行中' : state.destinationStateReady ? '已交接' : '已冻结');
        put(`destination-${kind}-text`, state.destinationRunning ? '运行中' : delivered ? '已接收' : '待接收');
        lab.querySelector(`[data-precopy-destination-${kind}]`).classList.toggle('is-ready', !!delivered);
      }
      scene.dataset.phase = state.phase;
      scene.classList.toggle('is-running-copy', !!sendingRam && after.sourceRunning);
      const channel = movement ? {ram: 'RAM', states: '状态', pause: '暂停', restore: '恢复'}[movement.kind] : state.destinationRunning ? '完成' : state.phase === 'final_ram' || state.phase === 'state_transfer' ? '状态' : 'RAM';
      put('channel-kind', channel);
      let message = '点击“下一步”，先把运行中源端的内存复制到目的端。';
      if (movement) {
        message = movement.kind === 'ram'
          ? `${after.phase === 'final_ram' ? '源端已暂停：补传最终 RAM，不再产生新脏页。' : '源端继续执行：已复制的内容若再次被写入，就需要重传。'} 数据到达后，右侧副本才更新。`
          : movement.kind === 'states' ? '传输冻结的 vCPU 与设备状态；目的端即使已收到全部 RAM，此时仍不能运行。'
          : movement.kind === 'pause' ? '冻结源端执行，结束运行中迭代。下一步单独传输最终 RAM。'
          : '目的端恢复 vCPU 与设备；恢复完成后才开始执行客户机。';
      } else if (state.phase === 'precopy') message = `目的端已有内存副本，其中 ${number(state.remainingMiB)} MiB 因源端继续写入而需要同步。`;
      else if (state.phase === 'paused') message = '源端已暂停，写入冻结；目的端等待最终 RAM 与执行状态。';
      else if (state.phase === 'final_ram') message = '最终 RAM 已到达，内存副本一致；下一步交接 vCPU 与设备状态，目的端还不能运行。';
      else if (state.phase === 'state_transfer') message = 'RAM、vCPU 与设备状态均已到达；下一步由目的端恢复执行。';
      else if (state.phase === 'complete') message = '目的端恢复运行，源端保持停止；任意时刻都没有两端同时执行。';
      put('transfer-message', message);
    }
    function svgElement(name, attributes, text) {
      const element = document.createElementNS('http://www.w3.org/2000/svg', name);
      for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
      if (text !== undefined) element.textContent = text;
      return element;
    }
    function chart(state) {
      const group = lab.querySelector('[data-precopy-chart-content]');
      const width = Math.max(240, Math.min(620, lab.querySelector('.precopy-chart').clientWidth));
      group.parentElement.setAttribute('viewBox', `0 0 ${width} 250`);
      const left = 75, right = width - 35, top = 25, bottom = 205;
      const maximum = state.parameters.memoryMiB;
      const last = Math.max(2, state.points.length - 1);
      const x = point => left + (right - left) * point / last;
      const y = value => bottom - (bottom - top) * value / maximum;
      const nodes = [];
      for (const value of [0, maximum / 2, maximum]) {
        nodes.push(svgElement('line', {x1: left, x2: right, y1: y(value), y2: y(value), class: 'precopy-grid'}));
        nodes.push(svgElement('text', {x: left - 8, y: y(value) + 4, 'text-anchor': 'end', class: 'precopy-axis'}, number(value)));
      }
      nodes.push(svgElement('text', {x: left, y: 14, class: 'precopy-axis'}, 'MiB'));
      if (state.thresholdMiB <= maximum) {
        nodes.push(svgElement('line', {x1: left, x2: right, y1: y(state.thresholdMiB), y2: y(state.thresholdMiB), class: 'precopy-threshold'}));
      }
      put('threshold-key', state.thresholdMiB <= maximum ? '虚线：切换阈值' : `切换阈值 ${number(state.thresholdMiB)} MiB，高于纵轴范围`);
      nodes.push(svgElement('polyline', {points: state.points.map((point, index) => `${x(index)},${y(point.remainingMiB)}`).join(' '), class: 'precopy-curve'}));
      state.points.forEach((point, position) => {
        const label = position === 0 ? '初始' : point.final ? '停机后' : `第 ${point.round} 轮`;
        const circle = svgElement('circle', {cx: x(position), cy: y(point.remainingMiB), r: position === state.points.length - 1 ? 5 : 3, class: 'precopy-point'});
        circle.append(svgElement('title', {}, `${label}：${number(point.remainingMiB)} MiB`));
        nodes.push(circle);
        if (position === 0 || position === state.points.length - 1 || (width >= 480 && state.points.length < 7 && position < state.points.length - 1)) {
          nodes.push(svgElement('text', {x: x(position), y: 231, 'text-anchor': 'middle', class: 'precopy-axis'}, label));
        }
      });
      group.replaceChildren(...nodes);
      put('chart-description', `初始 ${number(state.parameters.memoryMiB)} MiB；${state.points.slice(1).map(point => `${point.final ? '停机后' : `第 ${point.round} 轮后`} ${number(point.remainingMiB)} MiB`).join('；')}。切换阈值 ${number(state.thresholdMiB)} MiB。`);
    }
    function draw() {
      const state = history[index];
      drawControls();
      lab.querySelectorAll('[data-precopy-preset]').forEach(button => {
        const preset = PRESETS[button.dataset.precopyPreset];
        button.setAttribute('aria-pressed', String(!invalid && Object.keys(LIMITS).every(key => state.parameters[key] === preset[key])));
      });
      lab.querySelectorAll('[data-precopy-bandwidth]').forEach(button => {
        button.setAttribute('aria-pressed', String(!invalid && state.parameters.bandwidthMiBps === Number(button.dataset.precopyBandwidth)));
      });
      output.hidden = invalid;
      lab.querySelector('[data-precopy-details]').hidden = invalid;
      if (invalid) { status.textContent = '请先修正参数，再开始演示。'; return; }
      const timing = estimateDowntime(state);
      const stage = {ready: '尚未开始', precopy: `第 ${state.rounds} 轮结束`, paused: '源端已暂停', final_ram: '最终 RAM 已到达', state_transfer: 'vCPU 与设备状态已到达', complete: '目的端恢复运行'}[state.phase];
      status.textContent = state.observationEnded ? `观察结束 · 第 ${state.rounds} 轮后仍未达到切换条件` : stage;
      let description;
      if (state.phase === 'ready') {
        description = `首先传输 ${number(state.remainingMiB)} MiB 初始内存；客户机继续运行，复制期间写脏的页面会进入下一轮。`;
      } else if (state.phase === 'paused') {
        description = `待传 ${number(state.remainingMiB)} MiB ≤ 阈值 ${number(state.thresholdMiB)} MiB。源端暂停，客户机不再产生脏页；下一步仅传输最终 RAM。`;
      } else if (state.phase === 'final_ram') {
        description = `最终 ${number(state.stopRemainingMiB)} MiB RAM 已传完，用时 ${milliseconds(timing.ramMs)}。目的端尚无完整执行状态，不能立即运行；下一步保存并传输 vCPU 与设备状态。`;
      } else if (state.phase === 'state_transfer') {
        description = 'vCPU 与设备状态已到达，示例中的保存与传输耗时为 20 ms。目的端还需要恢复这些状态，下一步才开始执行客户机。';
      } else if (state.phase === 'complete') {
        description = `完成 ${state.rounds} 轮运行中复制，最终补传 ${number(state.stopRemainingMiB)} MiB。模型总耗时 ${duration(state.elapsedSeconds)}，其中停机 ${milliseconds(state.downtimeMs)}；源端保持停止，目的端恢复运行。`;
      } else {
        description = `${state.rounds === 1 ? '首次复制' : '脏页重传'} ${number(state.lastTransferMiB)} MiB，用时 ${duration(state.lastDurationSeconds)}；期间形成 ${number(state.remainingMiB)} MiB 待传脏页。`;
        if (state.remainingMiB <= state.thresholdMiB) description += ' 已达到模型切换条件，下一步暂停源端。';
        else if (state.observationEnded) description += state.endReason === 'stable'
          ? ` 连续 ${STABLE_ROUNDS} 轮没有减少，演示停止观察，不代表迁移已完成或 QEMU 自动失败。可提高带宽或降低脏页速率后重试。`
          : ` 已观察 ${MAX_ROUNDS} 轮，演示停止观察；仍在缓慢下降，但尚未达到切换条件，未强行暂停或判定迁移失败。`;
        else description += ` 仍高于 ${number(state.thresholdMiB)} MiB 的阈值，继续迭代。`;
      }
      put('description', description);
      const stats = {
        remaining: `${number(state.remainingMiB)} MiB`,
        step: state.phase === 'ready' || state.phase === 'paused' ? '本步未传输' : state.phase === 'state_transfer' ? '状态 / 20 ms' : state.phase === 'complete' ? '恢复 / 20 ms' : `${number(state.lastTransferMiB)} MiB / ${duration(state.lastDurationSeconds)}`,
        transferred: `${number(state.transferredMiB)} MiB`, elapsed: duration(state.elapsedSeconds)
      };
      lab.querySelectorAll('[data-precopy-stat]').forEach(item => { item.textContent = stats[item.dataset.precopyStat]; });
      put('tail-time', milliseconds(timing.ramMs));
      put('downtime', milliseconds(timing.totalMs));
      put('timing-title', state.phase === 'complete' ? '模型停机时间分解' : !state.sourceRunning ? '停机阶段时间分解' : '若现在暂停，停机时间估算');
      put('threshold', `目标 ${milliseconds(state.parameters.downtimeMs)} → RAM 切换阈值 ${number(state.parameters.bandwidthMiBps)} MiB/s × ${number(state.parameters.downtimeMs / 1000)} s = ${number(state.thresholdMiB)} MiB。${timing.totalMs > state.parameters.downtimeMs ? '加入额外开销后，停机估算仍可能超过目标。' : '目标是切换判据的输入，不是停机时间的硬保证。'}`);
      chart(state);
      drawScene(state, transition);
    }
    function restart() {
      stop(true);
      try {
        const parameters = {};
        for (const input of inputs) parameters[input.dataset.precopyParameter] = input.value.trim() === '' ? NaN : Number(input.value);
        const state = createState(parameters);
        history = [state]; index = 0; invalid = false;
        error.hidden = true; error.textContent = '';
        inputs.forEach(input => input.removeAttribute('aria-invalid'));
      } catch (reason) {
        invalid = true; error.textContent = reason.message; error.hidden = false;
        inputs.forEach(input => input.setAttribute('aria-invalid', String(!input.checkValidity() || input.value.trim() === '')));
      }
      draw();
    }
    function commitTransition() {
      if (!transition) return;
      const after = transition.after;
      if (index + 1 === history.length) history.push(after);
      index += 1;
      transition = null; frame = null; lastFrame = null;
      flights.replaceChildren();
      if (finished(after)) stop();
      draw();
      if (playing) timer = root.setTimeout(() => { timer = null; forward(); }, reducedMotion.matches ? 1800 : 900);
    }
    function animate(timestamp) {
      if (!transition) return;
      if (lastFrame !== null) transition.elapsed += Math.min(100, timestamp - lastFrame);
      lastFrame = timestamp;
      for (const token of transition.tokens) {
        const progress = Math.max(0, Math.min(1, (transition.elapsed - token.delay) / token.duration));
        token.progress = progress;
        token.element.hidden = progress <= 0 || progress >= 1;
        const x = token.start.x + (token.end.x - token.start.x) * progress;
        const y = token.start.y + (token.end.y - token.start.y) * progress - Math.sin(Math.PI * progress) * 28;
        token.element.style.transform = `translate(${x}px, ${y}px)`;
      }
      drawScene(history[index], transition);
      if (transition.elapsed >= transition.duration) { commitTransition(); return; }
      frame = root.requestAnimationFrame(animate);
    }
    function forward() {
      if (invalid || transition || finished(history[index])) return;
      const before = history[index];
      const after = index + 1 < history.length ? history[index + 1] : advance(before);
      const kind = after.phase === 'precopy' || after.phase === 'final_ram' ? 'ram' : after.phase === 'state_transfer' ? 'states' : after.phase === 'complete' ? 'restore' : 'pause';
      transition = {after, kind, elapsed: 0, duration: kind === 'pause' ? 600 : 900, tokens: []};
      if (reducedMotion.matches) { commitTransition(); return; }
      const bounds = scene.getBoundingClientRect();
      function center(element) {
        const box = element.getBoundingClientRect();
        return {x: box.x + box.width / 2 - bounds.x - 13, y: box.y + box.height / 2 - bounds.y - 12};
      }
      function token(source, destination, label, info, position) {
        const element = document.createElement('span');
        element.className = `precopy-flight${kind === 'states' ? ' precopy-flight--state' : ''}`;
        element.textContent = label; element.hidden = true;
        flights.append(element);
        transition.tokens.push({...info, element, progress: 0, delay: position * 100, duration: 1050, start: center(source), end: center(destination)});
      }
      if (kind === 'ram') {
        const regions = memoryRegions(before);
        regions.forEach((region, position) => {
          if (region.dirtyFraction > 0) token(memory.source[position], memory.destination[position], region.label, {region: position}, position);
        });
      } else if (kind === 'states') {
        ['cpu', 'device'].forEach((name, position) => token(lab.querySelector(`[data-precopy-source-${name}]`), lab.querySelector(`[data-precopy-destination-${name}]`), name === 'cpu' ? 'CPU' : '设备', {kind: name}, position * 4));
      }
      if (transition.tokens.length) transition.duration = Math.max(...transition.tokens.map(item => item.delay + item.duration)) + 150;
      drawScene(before, transition);
      status.textContent = {ram: after.phase === 'final_ram' ? '传输最终 RAM' : `第 ${after.rounds} 轮 RAM 传输中`, states: '交接 vCPU 与设备状态', restore: '目的端恢复中', pause: '暂停源端'}[kind];
      frame = root.requestAnimationFrame(animate);
      drawControls();
    }
    inputs.forEach(input => input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', restart));
    lab.querySelectorAll('[data-precopy-bandwidth]').forEach(button => button.addEventListener('click', () => {
      const bandwidth = inputs.find(input => input.dataset.precopyParameter === 'bandwidthMiBps');
      bandwidth.value = button.dataset.precopyBandwidth;
      restart();
    }));
    lab.querySelectorAll('[data-precopy-preset]').forEach(button => button.addEventListener('click', () => {
      const preset = PRESETS[button.dataset.precopyPreset];
      inputs.forEach(input => { input.value = preset[input.dataset.precopyParameter]; });
      restart();
    }));
    next.addEventListener('click', () => { stop(); forward(); });
    previous.addEventListener('click', () => { const inFlight = !!transition; stop(true); if (!invalid && !inFlight && index > 0) index -= 1; draw(); });
    reset.addEventListener('click', restart);
    play.addEventListener('click', () => {
      if (playing || frame !== null) stop();
      else if (!invalid && !finished(history[index])) {
        playing = true;
        status.setAttribute('aria-live', 'off');
        if (transition) frame = root.requestAnimationFrame(animate);
        else forward();
      }
      drawControls();
    });
    reducedMotion.addEventListener('change', () => { stop(true); draw(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) { stop(); drawControls(); } });
    root.addEventListener('resize', () => { if (!invalid) { stop(true); draw(); } });
    interactive.hidden = false; draw(); lab.dataset.initialized = 'true';
  }
  document.querySelectorAll('[data-migration-lab]').forEach(initialize);
})(typeof globalThis !== 'undefined' ? globalThis : this);
