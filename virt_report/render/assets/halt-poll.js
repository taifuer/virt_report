/* A deterministic halt/wake teaching model, not a host scheduler simulator. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.querySelectorAll('[data-halt-poll-lab]').forEach(lab => api.mount(lab, root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  const MAX_POLL_US = 200, PREPARE_US = 10, GROW_START_US = 10;
  const DEFAULTS = Object.freeze({eventUs: 30, pollUs: 50, dispatchUs: 40});
  const LABELS = {poll: '轮询', prepare: '等待准备', blocked: '睡眠', dispatch: '已唤醒，待调度'};
  const roundUs = value => Math.round(value * 1000) / 1000;

  function integer(value, name, limit) {
    const text = typeof value === 'string' ? value.trim() : '';
    const number = typeof value === 'number' ? value : (/^[0-9]+$/.test(text) ? Number(text) : NaN);
    if (!Number.isSafeInteger(number) || number < 0 || number > limit) {
      const error = new RangeError(`${name}需要是 0—${limit} 之间的整数。`);
      throw error;
    }
    return number;
  }

  function microseconds(value, name, limit) {
    const text = typeof value === 'string' ? value.trim() : '';
    const number = typeof value === 'number' ? value : (/^[0-9]+(?:\.[0-9]{1,3})?$/.test(text) ? Number(text) : NaN);
    const nanoseconds = Math.round(number * 1000);
    if (!Number.isSafeInteger(nanoseconds) || number < 0 || number > limit || Math.abs(nanoseconds - number * 1000) > 1e-7) {
      throw new RangeError(`${name}需在 0—${limit} 之间，最多三位小数。`);
    }
    return nanoseconds / 1000;
  }

  function normalize(config = {}) {
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new TypeError('invalid halt configuration');
    const result = {};
    for (const [key, name, limit] of [['eventUs', '事件到达（µs）', 1000], ['pollUs', '轮询预算（µs）', MAX_POLL_US], ['dispatchUs', '调度等待（µs）', 200]]) {
      try { result[key] = (key === 'pollUs' ? microseconds : integer)(config[key] === undefined ? DEFAULTS[key] : config[key], name, limit); }
      catch (error) { error.field = key; throw error; }
    }
    return result;
  }

  // Mirrors the valid-wakeup branch and the next-entry cap in Linux 6.18.
  // Times are expressed in microseconds with nanosecond precision.
  function adapt(pollUs, haltUs, maxUs = MAX_POLL_US) {
    pollUs = microseconds(pollUs, '预算', 1000);
    haltUs = microseconds(haltUs, '总耗时', 2000);
    maxUs = microseconds(maxUs, '上限', 1000);
    const current = Math.min(pollUs, maxUs);
    let stored = current, action = '保持';
    if (!maxUs) { stored = 0; action = '关闭'; }
    else if (haltUs <= current) { /* Keep an adequate window. */ }
    else if (current && haltUs > maxUs) {
      stored = Math.floor(Math.round(current * 1000) / 2) / 1000;
      if (stored < GROW_START_US) stored = 0;
      action = '缩小';
    } else if (current < maxUs && haltUs < maxUs) {
      stored = Math.max(roundUs(current * 2), GROW_START_US);
      action = '增长';
    }
    return {currentUs: current, storedUs: stored, nextUs: Math.min(stored, maxUs), action};
  }

  function simulate(config = {}) {
    const options = normalize(config), {eventUs, pollUs, dispatchUs} = options;
    const segments = [], events = [];
    const segment = (kind, start, end) => { if (end > start) segments.push({kind, start, end}); };
    const event = (at, text) => events.push({at, text});
    let outcome, returnUs, pollingUs, prepareUs = 0, slept = false;
    event(0, '进入 halt 路径，检查是否可以停止等待。');
    if (eventUs === 0) {
      outcome = 'pending'; returnUs = 0; pollingUs = 0;
      event(0, '首次检查已看到可运行事件，无需睡眠。');
    } else if (eventUs < pollUs) {
      outcome = 'poll'; returnUs = eventUs; pollingUs = eventUs;
      segment('poll', 0, eventUs);
      event(eventUs, '轮询观察到事件，本次 halt 返回。');
    } else {
      pollingUs = pollUs;
      prepareUs = PREPARE_US;
      const checkedAt = roundUs(pollUs + PREPARE_US);
      segment('poll', 0, pollUs);
      segment('prepare', pollUs, checkedAt);
      event(pollUs, pollUs ? '轮询预算用完，发布等待者并准备复查。' : '预算为零，直接发布等待者并准备复查。');
      if (eventUs <= checkedAt) {
        outcome = 'recheck'; returnUs = checkedAt;
        event(eventUs, '事件在等待准备期间到达，pending 状态被保留。');
        event(checkedAt, '睡眠前复查发现事件，不调用 schedule。');
      } else {
        outcome = 'sleep'; slept = true; returnUs = eventUs + dispatchUs;
        segment('blocked', checkedAt, eventUs);
        segment('dispatch', eventUs, returnUs);
        event(checkedAt, '复查仍无事件，调用 schedule 并睡眠。');
        event(eventUs, '事件到达并唤醒线程；线程变为可运行。');
        event(returnUs, '取得宿主 CPU，再次检查后本次 halt 返回。');
      }
    }
    return {
      config: options, outcome, returnUs, pollingUs, prepareUs, slept,
      eventToReturnUs: roundUs(returnUs - eventUs),
      activeUs: roundUs(pollingUs + prepareUs),
      segments, events, adaptation: adapt(pollUs, returnUs)
    };
  }

  function mount(lab, environment) {
    const doc = environment.document, get = name => lab.querySelector(`[data-halt-poll-${name}]`);
    const controls = {eventUs: get('event'), pollUs: get('poll'), dispatchUs: get('dispatch')};
    const make = (tag, text, className) => {
      const node = doc.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    let state;
    function refresh() {
      for (const control of Object.values(controls)) control.removeAttribute('aria-invalid');
      try { state = simulate(Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value]))); }
      catch (error) {
        state = null;
        get('scene').hidden = true; get('error').hidden = false; get('error').textContent = error.message;
        if (controls[error.field]) controls[error.field].setAttribute('aria-invalid', 'true');
        return;
      }
      get('error').hidden = true; get('error').textContent = ''; get('scene').hidden = false;
      const descriptions = {pending: '事件已 pending：无需等待', poll: '轮询内观察到事件：没有睡眠', recheck: '睡眠前复查命中：没有睡眠', sleep: '轮询未覆盖事件：睡眠后被唤醒'};
      const status = `${descriptions[state.outcome]}。事件在 ${state.config.eventUs} µs 到达，本次 halt 在 ${state.returnUs} µs 返回。`;
      get('status').textContent = status;
      get('timeline').setAttribute('aria-label', status + state.segments.map(s => `${s.start}—${s.end} µs：${LABELS[s.kind]}`).join('；'));
      get('track').replaceChildren();
      for (const segment of state.segments) {
        const node = make('span', '', 'halt-poll-segment');
        node.dataset.kind = segment.kind;
        node.style.width = `${(segment.end - segment.start) / Math.max(state.returnUs, 1) * 100}%`;
        node.title = `${LABELS[segment.kind]}：${segment.start}—${segment.end} µs`;
        node.setAttribute('aria-hidden', 'true');
        get('track').append(node);
      }
      const marker = make('span', '', 'halt-poll-marker');
      marker.style.left = `${state.returnUs ? state.config.eventUs / state.returnUs * 100 : 0}%`;
      marker.title = `事件到达：${state.config.eventUs} µs`; marker.setAttribute('aria-hidden', 'true');
      get('track').append(marker);
      get('end').textContent = `${state.returnUs} µs · halt 返回`;
      get('events').replaceChildren(...state.events.map(e => make('li', `${e.at} µs · ${e.text}`)));
      get('summary').replaceChildren(
        make('p', `本轮轮询 ${state.pollingUs} µs`),
        make('p', `事件到返回 ${state.eventToReturnUs} µs`),
        make('p', `下轮有效预算 ${state.adaptation.nextUs} µs`)
      );
      const a = state.adaptation;
      get('adaptation').textContent = `按有效唤醒分支计算：halt 总耗时 ${state.returnUs} µs，预算${a.action}为 ${a.storedUs} µs。${a.storedUs > MAX_POLL_US ? `下次入口裁剪到上限 ${MAX_POLL_US} µs。` : '下轮事件时刻仍需重新观察。'}`;
      get('next').disabled = a.nextUs === state.config.pollUs;
    }
    function set(config) {
      for (const [key, value] of Object.entries(config)) controls[key].value = String(value);
      refresh();
    }
    get('controls').addEventListener('submit', event => { event.preventDefault(); refresh(); });
    for (const control of Object.values(controls)) control.addEventListener('input', refresh);
    get('inside').addEventListener('click', () => set(DEFAULTS));
    get('after').addEventListener('click', () => set({eventUs: 120, pollUs: 50, dispatchUs: 40}));
    get('race').addEventListener('click', () => set({eventUs: 55, pollUs: 50, dispatchUs: 40}));
    get('reset').addEventListener('click', () => set(DEFAULTS));
    get('next').addEventListener('click', () => { if (state) set({...state.config, pollUs: state.adaptation.nextUs}); });
    refresh(); get('controls').hidden = false; get('fallback').hidden = true;
    return {state: () => state, refresh};
  }
  return {MAX_POLL_US, PREPARE_US, GROW_START_US, DEFAULTS, normalize, adapt, simulate, mount};
});
