'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/tcg-tb.js');

test('B ends at its code-page boundary and falls through into C', () => {
  assert.equal(model.BLOCKS.B.pc, 0x1fe0);
  assert.equal(model.BLOCKS.B.end + 1, model.BLOCKS.C.pc);
  assert.equal(model.BLOCKS.A.page, model.BLOCKS.B.page);
  assert.notEqual(model.BLOCKS.B.page, model.BLOCKS.C.page);
  for (const block of Object.values(model.BLOCKS)) assert.equal(block.end - block.pc + 1, 32);
});

test('cold entry translates A but does not invent a predecessor link', () => {
  const state = model.step(model.initial());
  assert.equal(state.last.kind, 'translate'); assert.equal(state.pc, 'B');
  assert.deepEqual(state.cache, {A: {generation: 1, valid: true}});
  assert.deepEqual(state.links, {}); assert.equal(state.executed, 1);
});
test('same-page branches link on first resolution and then bypass lookup', () => {
  let state = model.initial();
  state = model.step(state); state = model.step(state);
  assert.equal(state.last.newLink, 'A → B'); assert.equal(state.last.kind, 'translate');
  state = model.step(state);
  assert.equal(state.last.kind, 'dispatch-hit'); assert.equal(state.last.newLink, 'B → A');
  state = model.step(state);
  assert.equal(state.last.kind, 'direct'); assert.equal(state.last.newLink, null);
  assert.equal(state.cache.B.generation, 1); assert.equal(Object.keys(state.links).length, 2);
});
test('cross-page cold misses translate while warm branches use helper lookup', () => {
  let state = model.initial();
  state = model.step(state); state = model.step(state, 'C'); state = model.step(state, 'C');
  assert.equal(state.last.kind, 'translate'); assert.equal(state.last.block, 'C');
  assert.match(state.last.stages[0], /helper/);
  state = model.step(state, 'C'); assert.equal(state.last.kind, 'helper-hit');
  state = model.step(state, 'C'); assert.equal(state.last.kind, 'helper-hit');
  assert.deepEqual(Object.keys(state.links), ['A>B']);
  assert.equal(state.cache.C.generation, 1);
});
test('changing a conditional outcome preserves existing code and links', () => {
  let state = model.initial(); for (let i = 0; i < 3; i++) state = model.step(state);
  state = model.step(state, 'C');
  assert.equal(state.last.kind, 'direct'); assert.equal(state.pc, 'C');
  assert.equal(state.cache.B.generation, 1); assert.ok(state.links['B>A']);
});
test('overlapping write invalidates both code ranges and removes links, not C', () => {
  let state = model.initial(); for (let i = 0; i < 3; i++) state = model.step(state);
  state = model.step(state, 'C'); state = model.step(state);
  const previous = state;
  state = model.invalidate(state);
  assert.deepEqual(state.last.affected, ['A#1', 'B#1']);
  assert.equal(state.cache.A.valid, false); assert.equal(state.cache.B.valid, false);
  assert.equal(state.cache.C.valid, true); assert.deepEqual(state.links, {});
  assert.equal(state.previous, null); assert.equal(previous.cache.A.valid, true);
});
test('invalidated entries regenerate with fresh generation and fresh links', () => {
  let state = model.initial(); for (let i = 0; i < 4; i++) state = model.step(state);
  state = model.invalidate(state); state = model.step(state);
  assert.equal(state.last.kind, 'translate'); assert.equal(state.last.generation, 2);
  state = model.step(state);
  assert.equal(state.last.kind, 'translate'); assert.deepEqual(state.links['A>B'], {from: 2, to: 2});
  state = model.step(state); state = model.step(state); assert.equal(state.last.kind, 'direct');
});
test('an unaffected C remains a helper hit after A and B are invalidated', () => {
  let state = model.initial();
  state = model.step(state); state = model.step(state, 'C'); state = model.step(state);
  state = model.invalidate(state); state = model.step(state, 'C');
  assert.equal(state.last.block, 'B'); assert.equal(state.last.kind, 'translate');
  state = model.step(state);
  assert.equal(state.last.block, 'C'); assert.equal(state.last.kind, 'helper-hit');
  assert.equal(state.last.generation, 1);
});
test('invalidating an empty or already invalid cache does not invent generations', () => {
  let state = model.invalidate(model.initial());
  assert.deepEqual(state.last.affected, []); assert.deepEqual(state.cache, {});
  state = model.step(state); state = model.invalidate(state); state = model.invalidate(state);
  assert.deepEqual(state.last.affected, []); assert.equal(state.cache.A.generation, 1);
});
test('manual reentry retains cache and links but performs a new lookup', () => {
  let state = model.initial(); for (let i = 0; i < 4; i++) state = model.step(state);
  state = model.reenter(state); assert.equal(state.pc, 'A'); assert.equal(state.previous, null);
  assert.equal(Object.keys(state.links).length, 2);
  state = model.step(state); assert.equal(state.last.kind, 'dispatch-hit');
  state = model.step(state); assert.equal(state.last.kind, 'direct');
});
test('branches are validated without coercion or state mutation', () => {
  const state = model.initial();
  for (const value of ['', 'B', 'a', null, true, [], {}, 0, ' A ']) assert.throws(() => model.step(state, value), RangeError);
  assert.deepEqual(state, model.initial());
});
test('long sequences retain bounded newest-first history and valid link generations', () => {
  let state = model.initial();
  for (let i = 0; i < 100; i++) {
    state = i % 9 === 0 ? model.invalidate(state) : model.step(state, i % 5 ? 'A' : 'C');
    for (const [edge, link] of Object.entries(state.links)) {
      const [from, to] = edge.split('>');
      assert.ok(state.cache[from].valid && state.cache[to].valid);
      assert.equal(link.from, state.cache[from].generation); assert.equal(link.to, state.cache[to].generation);
      assert.equal(model.BLOCKS[from].page, model.BLOCKS[to].page);
    }
  }
  assert.equal(state.history.length, 8); assert.equal(state.history[0].number, 100);
  assert.equal(state.history[7].number, 93);
});

class Element {
  constructor(tag = '') { this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {}; this.hidden = true; this.value = ''; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  fire(name, event = {}) { this.listeners[name]?.(event); }
}
function fixture() {
  const nodes = new Map(), lab = new Element();
  const get = name => { if (!nodes.has(name)) nodes.set(name, new Element()); return nodes.get(name); };
  lab.querySelector = selector => get(selector.match(/^\[data-tcg-tb-(.*)\]$/)[1]);
  get('branch').value = 'A';
  const controller = model.mount(lab, {document: {createElement: tag => new Element(tag)}});
  return {get, controller};
}
test('mount exposes controls, next PC, cache states, links and event trace', () => {
  const {get, controller} = fixture();
  assert.equal(get('controls').hidden, false); assert.equal(get('fallback').hidden, true);
  assert.equal(get('scene').hidden, false); assert.match(get('key').textContent, /PC=0x1000/);
  for (let i = 0; i < 4; i++) get('step').fire('click');
  assert.equal(controller.state().last.kind, 'direct'); assert.equal(get('links').children.length, 2);
  assert.equal(get('trace').children.length, 4); assert.match(get('status').textContent, /不进行 TB 查找/);
  assert.equal(get('page-one').children[0].dataset.current, 'true');
});
test('invalidation and reentry controls update state without auto-executing', () => {
  const {get, controller} = fixture(); get('step').fire('click'); get('step').fire('click');
  get('invalidate').fire('click'); assert.equal(controller.state().cache.A.valid, false);
  assert.equal(get('page-one').children[0].dataset.state, 'invalid');
  get('reenter').fire('click'); assert.equal(controller.state().executed, 2);
  get('step').fire('click'); assert.equal(controller.state().cache.A.generation, 2);
});
test('invalid input reports an error and reset clears history, errors and controls', () => {
  const {get, controller} = fixture(); get('branch').value = 'invalid'; get('step').fire('click');
  assert.equal(controller.state().executed, 0); assert.equal(get('error').hidden, false);
  assert.equal(get('branch').attributes['aria-invalid'], 'true');
  get('branch').value = 'C'; get('branch').fire('change');
  assert.equal(get('error').hidden, true); get('step').fire('click'); get('step').fire('click');
  assert.equal(controller.state().pc, 'C'); get('reset').fire('click');
  assert.deepEqual(controller.state(), model.initial()); assert.equal(get('branch').value, 'A');
  assert.equal(get('trace').children.length, 0); assert.equal(get('branch').attributes['aria-invalid'], undefined);
});
