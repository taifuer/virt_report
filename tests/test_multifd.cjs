'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../virt_report/render/assets/multifd.js');

function history(config) {
  const states = [model.createState(config)];
  while (states.at(-1).phase !== 'done' && states.length < 300) states.push(model.step(states.at(-1)));
  assert.equal(states.at(-1).phase, 'done');
  return states;
}
test('configuration permits supported deterministic presets only', () => {
  assert.deepEqual(model.normalizeConfig(), {channels: 4, delay: 3});
  assert.deepEqual(model.normalizeConfig({channels: '8', delay: '0'}), {channels: 8, delay: 0});
  for (const channels of [1, 3, 0, NaN]) assert.throws(() => model.createState({channels}), RangeError);
  for (const delay of [-1, 1, Infinity]) assert.throws(() => model.createState({delay}), RangeError);
});
test('step does not mutate previous state; reset reconstructs original state', () => {
  const initial = model.createState(), copy = JSON.stringify(initial);
  const next = model.step(initial);
  assert.equal(JSON.stringify(initial), copy); assert.notEqual(next, initial);
  assert.deepEqual(model.createState(initial.config), initial);
});
test('local completion precedes remote completion and cannot release destination barrier', () => {
  const states = history({channels: 4, delay: 6});
  const local = states.find(state => state.localDone);
  assert.equal(local.phase, 'sync'); assert.equal(local.mainArrived, false);
  assert.ok(local.destination.some(page => page === 0));
  const blocked = states.find(state => state.mainArrived && state.lanes.some(lane => !lane.syncArrived));
  assert.ok(blocked); assert.equal(blocked.phase, 'sync'); assert.equal(blocked.epoch, 1);
  assert.match(model.describe(blocked), /目的端主流等待/);
});
test('every barrier requires main FLUSH and all ordered data channel SYNC packets', () => {
  for (const channels of [2, 4, 8]) for (const delay of [0, 3, 6]) {
    for (const state of history({channels, delay})) {
      for (const lane of state.lanes) if (lane.syncArrived) {
        assert.equal(lane.queue.length, 0);
      }
      if (state.phase === 'barrier' || state.phase === 'done') {
        assert.equal(state.mainArrived, true); assert.ok(state.lanes.every(lane => lane.syncArrived));
        assert.deepEqual(state.destination, state.source);
      }
    }
  }
});
test('all channels participate even when second group has fewer batches than channels', () => {
  const states = history({channels: 8, delay: 3});
  const end = states.at(-1);
  assert.equal(end.totalDelivered, 24);
  assert.equal(end.lanes.filter(lane => lane.syncArrived).length, 8);
  assert.equal(end.assigned, 4);
});
test('two groups preserve old pages and replace only dirtied pages', () => {
  const states = history({channels: 2, delay: 6}), end = states.at(-1);
  assert.deepEqual(end.destination, [2, 2, 1, 1, 2, 2, 1, 1, 2, 2, 1, 1, 2, 2, 1, 1]);
  const first = states.find(state => state.epoch === 1 && state.phase === 'barrier');
  assert.deepEqual(first.destination, Array(16).fill(1));
  assert.equal(end.totalDelivered, 24); assert.equal(model.step(end), end);
});
test('added receiver delay changes waiting duration without changing final contents', () => {
  const fast = history({channels: 4, delay: 0}), slow = history({channels: 4, delay: 6});
  assert.ok(slow.length > fast.length);
  assert.deepEqual(slow.at(-1).destination, fast.at(-1).destination);
});
test('task distribution and history are deterministic for identical inputs', () => {
  const first = history({channels: 4, delay: 3});
  assert.deepEqual(first, history({channels: 4, delay: 3}));
  const dispatched = first[1];
  assert.deepEqual(dispatched.lanes.map(lane => lane.job.batch.id), ['1.1', '1.2', '1.3', '1.4']);
  for (const state of first) assert.ok(state.log.length <= 4);
});
test('article provides a no-JS flow and fullscreen hooks without global page overflow', () => {
  const template = fs.readFileSync(path.join(__dirname, '../virt_report/render/templates/_multifd_lab.html'), 'utf8');
  assert.match(template, /data-mechanism-view="lab"/);
  assert.match(template, /data-multifd-fallback>/);
  assert.match(template, /源端 QEMU/); assert.match(template, /目的端 QEMU/);
  assert.match(template, /主迁移流/); assert.match(template, /每步为逻辑时刻/);
});

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.style = {}; this.attributes = {};
    this.listeners = {}; this.hidden = true; this.disabled = false;
    this.value = ''; this.textContent = '';
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name) { if (!this.disabled && this.listeners[name]) this.listeners[name](); }
}
function fixture() {
  const elements = new Map(), lab = new Element(), document = new Element();
  const get = name => {
    if (!elements.has(name)) elements.set(name, new Element());
    return elements.get(name);
  };
  get('channels').value = '4'; get('delay').value = '3';
  lab.querySelector = selector => get(selector.match(/^\[data-multifd-(.*)\]$/)[1]);
  document.createElement = () => new Element();
  document.hidden = false;
  let id = 0; const timers = new Map();
  const environment = {
    document, setTimeout: callback => { timers.set(++id, callback); return id; },
    clearTimeout: key => timers.delete(key)
  };
  return {lab, document, get, timers, environment, tick() {
    const calls = [...timers.values()]; timers.clear(); calls.forEach(call => call());
  }};
}
test('mount starts idle and shows both hosts, per-channel rows and 16-page memories', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  assert.equal(controller.state().phase, 'ready'); assert.equal(f.timers.size, 0);
  assert.equal(f.get('controls').hidden, false); assert.equal(f.get('fallback').hidden, true);
  assert.equal(f.get('lanes').children.length, 4);
  assert.equal(f.get('source-pages').children.length, 16);
  assert.equal(f.get('destination-pages').children.length, 16);
  f.get('step').fire('click'); assert.equal(controller.state().tick, 1);
});
test('play, pause, reset, and parameter changes have no stale timer effects', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  f.get('play').fire('click'); assert.equal(f.get('play').attributes['aria-pressed'], 'true');
  f.tick(); assert.equal(controller.state().tick, 1);
  const late = [...f.timers.values()][0];
  f.get('reset').fire('click'); late();
  assert.equal(controller.state().tick, 0); assert.equal(f.timers.size, 0);
  assert.equal(f.get('play').attributes['aria-pressed'], 'false');
  f.get('channels').value = '8'; f.get('channels').fire('change');
  assert.equal(controller.state().lanes.length, 8); assert.equal(f.get('lanes').children.length, 8);
  f.get('delay').value = '0'; f.get('delay').fire('change');
  assert.equal(controller.state().config.delay, 0); assert.equal(controller.state().tick, 0);
});
test('playback stops when complete; reset re-enables controls', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  f.get('play').fire('click');
  for (let i = 0; i < 300 && f.timers.size; i++) f.tick();
  assert.equal(controller.state().phase, 'done'); assert.equal(f.timers.size, 0);
  assert.equal(f.get('play').disabled, true); assert.equal(f.get('step').disabled, true);
  assert.equal(f.get('play').attributes['aria-pressed'], 'false');
  f.get('reset').fire('click');
  assert.equal(f.get('play').disabled, false); assert.equal(f.get('step').disabled, false);
});
test('backgrounding pauses playback without resetting state', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  f.get('play').fire('click'); f.tick();
  f.document.hidden = true; f.document.fire('visibilitychange');
  assert.equal(f.timers.size, 0); assert.equal(controller.state().tick, 1);
  assert.equal(f.get('play').attributes['aria-pressed'], 'false');
});
