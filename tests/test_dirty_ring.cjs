'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../virt_report/render/assets/dirty-ring.js');
const act = (state, kind, page = 3, cpu = 0) => model.apply(state, {kind, page, cpu});
function flush(state) {
  for (const kind of ['harvest', 'reprotect', 'publish']) state = act(state, kind);
  for (let page = 0; page < 8; page++) if (state.migration[page]) state = act(state, 'read', page);
  return state;
}
function invariant(state) {
  const capacity = state.config.capacity;
  for (const ring of state.rings) {
    assert.ok(ring.reset <= ring.consumer && ring.consumer <= ring.producer);
    assert.ok(model.used(ring) >= 0 && model.used(ring) <= capacity);
    assert.equal(ring.slots.filter(slot => slot.flag !== 'free').length, model.used(ring));
    for (let index = ring.reset; index < ring.producer; index++) {
      assert.equal(ring.slots[index % capacity].flag, index < ring.consumer ? 'reset' : 'dirty');
    }
  }
  for (let page = 0; page < 8; page++) if (state.source[page] !== state.destination[page]) {
    assert.ok(state.pending[page] || state.migration[page] || state.rings.some(ring => ring.slots.some(slot => slot.page === page)), `P${page} modification was lost`);
  }
  assert.ok(state.log.length <= 4);
}
test('configuration supports a fixed set of illustrative usable capacities', () => {
  assert.deepEqual(model.normalizeConfig(), {capacity: 4});
  assert.deepEqual(model.normalizeConfig({capacity: '2'}), {capacity: 2});
  for (const capacity of [-1, 0, 1, 3, 16, NaN, Infinity]) assert.throws(() => model.createState({capacity}), RangeError);
  const state = model.createState();
  assert.throws(() => model.apply(state, {kind: 'skip'}), RangeError);
  assert.throws(() => act(state, 'write', 8), RangeError);
  assert.throws(() => act(state, 'write', 3, 2), RangeError);
});
test('all actions return independent state and never mutate their input', () => {
  let state = model.createState();
  for (const action of model.example) {
    const before = JSON.stringify(state), next = model.apply(state, action);
    assert.equal(JSON.stringify(state), before); assert.notEqual(next, state); state = next;
  }
});
test('a repeated write to a released shared mapping updates data without another log entry', () => {
  let state = act(model.createState(), 'write', 3, 0);
  state = act(state, 'write', 3, 1);
  assert.equal(state.source[3], 3); assert.equal(state.records, 1);
  assert.equal(state.rings[0].producer, 1); assert.equal(state.rings[1].producer, 0);
  assert.match(state.status, /无新增条目/); invariant(state);
});
test('harvesting populates the private slot bitmap and RESET but does not reprotect', () => {
  let state = act(model.createState(), 'write');
  state = act(state, 'harvest');
  assert.equal(state.pending[3], true); assert.equal(state.migration[3], false);
  assert.equal(state.protected[3], false); assert.equal(state.rings[0].slots[0].flag, 'reset');
  assert.equal(state.rings[0].consumer, 1); assert.equal(state.rings[0].reset, 0);
});
test('publication and page reading cannot bypass the reset handoff', () => {
  let state = act(act(model.createState(), 'write'), 'harvest');
  state = act(state, 'publish');
  assert.equal(state.blocked, true); assert.equal(state.migration[3], false); assert.equal(state.pending[3], true);
  state = act(state, 'read'); assert.equal(state.transfers, 0); assert.equal(state.destination[3], 1);
  state = act(state, 'reprotect'); assert.equal(state.protected[3], true);
  state = act(state, 'publish'); assert.equal(state.migration[3], true); assert.equal(state.pending[3], false);
  state = act(state, 'read'); assert.equal(state.destination[3], 2); invariant(state);
});
test('write between harvest and reset is covered by the subsequent read', () => {
  let state = act(act(model.createState(), 'write'), 'harvest');
  state = act(state, 'write'); assert.equal(state.records, 1);
  state = act(act(state, 'reprotect'), 'publish'); state = act(state, 'read');
  assert.equal(state.source[3], 3); assert.equal(state.destination[3], 3); invariant(state);
});
test('write after reset and before publication is retained as a new ring generation', () => {
  let state = act(act(act(model.createState(), 'write'), 'harvest'), 'reprotect');
  state = act(state, 'write', 3, 1); assert.equal(state.records, 2);
  state = act(act(state, 'publish'), 'read');
  assert.equal(state.destination[3], 3);
  assert.equal(state.rings[1].slots[0].page, 3); assert.equal(state.rings[1].slots[0].flag, 'dirty');
  state = flush(state); assert.equal(state.transfers, 2); assert.deepEqual(state.source, state.destination);
});
test('write after migration read leaves a record that updates the next copy', () => {
  let state = flush(act(model.createState(), 'write'));
  state = act(state, 'write', 3, 1);
  assert.equal(state.source[3], 3); assert.equal(state.destination[3], 2); invariant(state);
  state = flush(state); assert.equal(state.destination[3], 3); assert.equal(state.records, 2);
});
test('full ring pauses only its own vCPU and neither overwrites records nor writes memory', () => {
  let state = model.createState({capacity: 2});
  state = act(act(state, 'write', 0), 'write', 1);
  const slots = JSON.stringify(state.rings[0].slots);
  state = act(state, 'write', 2);
  assert.equal(state.blocked, true); assert.equal(state.source[2], 1); assert.equal(state.records, 2);
  assert.equal(JSON.stringify(state.rings[0].slots), slots);
  state = act(state, 'write', 2, 1); assert.equal(state.source[2], 2); assert.equal(state.records, 3);
  state = act(state, 'harvest'); state = act(state, 'write', 4);
  assert.equal(state.blocked, true); assert.equal(state.source[4], 1);
  state = act(state, 'reprotect'); state = act(state, 'write', 4);
  assert.equal(state.source[4], 2); invariant(state);
});
test('reset stops at unharvested records and does not clear new dirty addresses', () => {
  let state = act(act(model.createState(), 'write', 0), 'harvest');
  state = act(state, 'write', 1); state = act(state, 'reprotect');
  assert.equal(state.rings[0].reset, 1); assert.equal(state.rings[0].producer, 2);
  assert.equal(state.rings[0].slots[1].flag, 'dirty'); assert.equal(state.protected[1], false);
  state = act(state, 'publish'); assert.equal(state.migration[0], true); assert.equal(state.migration[1], false);
  state = flush(state); assert.deepEqual(state.source, state.destination); invariant(state);
});
test('independent ring cursors wrap without skipping entries or treating the ring as a global queue', () => {
  let state = model.createState({capacity: 2});
  for (let round = 0; round < 10; round++) {
    state = act(state, 'write', round % 8, round % 2); state = flush(state); invariant(state);
  }
  assert.equal(state.rings[0].producer, 5); assert.equal(state.rings[1].producer, 5);
  assert.equal(state.rings[0].reset, 5); assert.equal(state.records, 10); assert.equal(state.transfers, 10);
});
test('supplied example demonstrates blocked early publication and converges at every capacity', () => {
  for (const capacity of [2, 4, 8]) {
    let state = model.createState({capacity}); let blocked = 0;
    for (const action of model.example) { state = model.apply(state, action); blocked += Number(state.blocked); invariant(state); }
    assert.equal(blocked, 1); assert.equal(state.records, 3); assert.equal(state.transfers, 3);
    assert.deepEqual(state.source, state.destination); assert.ok(state.rings.every(ring => model.used(ring) === 0));
  }
});
test('deterministic mixed operations preserve every unsent update and queue invariant', () => {
  const kinds = ['write', 'write', 'write', 'harvest', 'reprotect', 'publish', 'read'];
  for (const capacity of [2, 4, 8]) {
    let seed = 1769, state = model.createState({capacity});
    const random = limit => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return (seed >>> 7) % limit; };
    for (let i = 0; i < 1800; i++) {
      state = act(state, kinds[random(kinds.length)], random(8), random(2)); invariant(state);
    }
    state = flush(state); assert.deepEqual(state.source, state.destination); invariant(state);
  }
});
test('article retains a complete no-JS explanation, keyboard controls and shared fullscreen hooks', () => {
  const template = fs.readFileSync(path.join(__dirname, '../virt_report/render/templates/_dirty_ring_lab.html'), 'utf8');
  assert.match(template, /data-mechanism-view="lab"/); assert.match(template, /data-mechanism-expand hidden/);
  assert.match(template, /data-dirty-ring-fallback>/); assert.match(template, /目的端/);
  assert.match(template, /role="status" aria-live="polite"/); assert.match(template, /演示可用槽位/);
  assert.match(template, /硬件缓存及其预留槽位未展开/);
  const css = fs.readFileSync(path.join(__dirname, '../virt_report/render/assets/dirty-ring.css'), 'utf8');
  assert.match(css, /prefers-reduced-motion:reduce/); assert.match(css, /focus-visible/);
});

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.style = {setProperty(name, value) { this[name] = value; }};
    this.listeners = {}; this.hidden = true; this.value = ''; this.textContent = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name) { if (this.listeners[name]) this.listeners[name](); }
}
function fixture() {
  const elements = new Map(), lab = new Element(), document = {createElement: () => new Element()};
  const get = name => { if (!elements.has(name)) elements.set(name, new Element()); return elements.get(name); };
  get('capacity').value = '4'; get('cpu').value = '0'; get('page').value = '3';
  lab.querySelector = selector => get(selector.match(/^\[data-dirty-ring-(.*)\]$/)[1]);
  return {lab, get, environment: {document}};
}
test('mount exposes two rings, two bitmap layers and both memories without autoplay', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  assert.equal(controller.state().tick, 0); assert.equal(f.get('controls').hidden, false);
  assert.equal(f.get('fallback').hidden, true); assert.equal(f.get('rings').children.length, 2);
  for (const name of ['pending', 'migration', 'source', 'destination']) assert.equal(f.get(name).children.length, 8);
  f.get('write').fire('click'); assert.equal(controller.state().source[3], 2);
  f.get('harvest').fire('click'); f.get('publish').fire('click'); assert.equal(f.lab.dataset.blocked, 'true');
  f.get('reprotect').fire('click'); f.get('publish').fire('click'); f.get('read').fire('click');
  assert.equal(controller.state().destination[3], 2);
});
test('changing capacity or restarting resets all state, and example updates selected page and cpu', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  f.get('write').fire('click'); f.get('capacity').value = '2'; f.get('capacity').fire('change');
  assert.equal(controller.state().config.capacity, 2); assert.equal(controller.state().tick, 0);
  for (let i = 0; i < model.example.length; i++) f.get('demo').fire('click');
  assert.deepEqual(controller.state().source, controller.state().destination);
  assert.equal(controller.state().records, 3); assert.equal(f.get('demo').textContent, '重新演示');
  f.get('demo').fire('click'); assert.equal(controller.state().tick, 1); assert.equal(f.get('cpu').value, '0');
  f.get('reset').fire('click'); assert.equal(controller.state().tick, 0); assert.equal(f.get('demo').textContent, '示例下一步');
});
