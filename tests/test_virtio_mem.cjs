'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../virt_report/render/assets/virtio-mem.js');
const step = state => model.apply(state, {kind: 'step'});
const target = (state, value) => model.apply(state, {kind: 'target', value});
const block = (state, index) => model.apply(state, {kind: 'block', index});
const busy = (state, value) => model.apply(state, {kind: 'busy', value});
function invariant(state) {
  const amount = model.totals(state);
  assert.ok(state.requested >= 0 && state.requested <= model.capacity);
  assert.ok(state.usable >= state.requested && state.usable <= model.capacity);
  assert.ok(amount.plugged >= amount.online && amount.plugged <= state.usable);
  assert.ok(state.cells.every(cell => (!cell.online || cell.plugged) && (!cell.blocked || cell.online)));
  const pending = state.pending;
  if (pending) {
    assert.ok(['online', 'unplug'].includes(pending.kind));
    assert.equal(state.cells[pending.index].plugged, true);
    assert.equal(state.cells[pending.index].online, false);
  }
  assert.equal(state.cells.filter(cell => cell.plugged && !cell.online).length, pending ? 1 : 0);
  assert.ok(state.log.length <= 4);
}
function process(state, count = 80) {
  for (let i = 0; i < count; i++) { state = step(state); invariant(state); }
  return state;
}
test('initial model separates backend capacity, usable range and actual pages', () => {
  const state = model.createState();
  assert.equal(model.capacity, 8192); assert.equal(model.cellSize / model.deviceBlock, 256);
  assert.equal(state.cells.length, 16); assert.equal(state.requested, 4096); assert.equal(state.usable, 4352);
  assert.deepEqual(model.totals(state), {plugged: 4096, online: 4096, blocked: 0}); invariant(state);
});
test('configuration accepts only aligned illustrative capacity and valid actions', () => {
  for (const value of [-512, 1, 2, 511, 8704, NaN, Infinity, 'bad']) assert.throws(() => target(model.createState(), value), RangeError);
  for (const index of [-1, 16, 1.1, NaN]) assert.throws(() => block(model.createState(), index), RangeError);
  assert.throws(() => busy(model.createState(), 1), TypeError);
  assert.throws(() => model.apply(model.createState(), {kind: 'force'}), RangeError);
  assert.throws(() => model.apply(model.createState()), RangeError);
  for (let value = 0; value <= 8192; value += 512) invariant(target(model.createState(), String(value)));
});
test('target setter sends a request without immediately providing or removing memory', () => {
  const initial = model.createState();
  const up = target(initial, 6144), down = target(initial, 1024);
  assert.deepEqual(model.totals(up), model.totals(initial)); assert.deepEqual(model.totals(down), model.totals(initial));
  assert.equal(up.usable, 6400); assert.equal(down.usable, 4352); assert.equal(initial.requested, 4096);
});
test('PLUG acknowledgment precedes customer online operation', () => {
  let state = step(target(model.createState(), 6144));
  assert.equal(model.totals(state).plugged, 4608); assert.equal(model.totals(state).online, 4096);
  assert.deepEqual(state.pending, {kind: 'online', index: 8}); invariant(state);
  state = step(state);
  assert.equal(model.totals(state).online, 4608); assert.equal(state.pending, null); invariant(state);
  state = process(state); assert.equal(model.totals(state).plugged, 6144); assert.equal(model.totals(state).online, 6144);
});
test('shrink first isolates guest pages, then asks QEMU to discard and confirm', () => {
  let state = step(target(model.createState(), 1024));
  assert.equal(model.totals(state).plugged, 4096); assert.equal(model.totals(state).online, 3584);
  assert.deepEqual(state.pending, {kind: 'unplug', index: 7}); invariant(state);
  state = step(state);
  assert.equal(model.totals(state).plugged, 3584); assert.equal(model.totals(state).online, 3584);
  assert.equal(state.cells[7].plugged, false); invariant(state);
  state = process(state); assert.equal(model.totals(state).plugged, 1024);
});
test('ordinary shrink including zero keeps the expanded usable range', () => {
  let state = process(target(model.createState(), 8192));
  assert.equal(state.usable, 8192); assert.equal(model.totals(state).plugged, 8192);
  state = process(target(state, 0));
  assert.equal(model.totals(state).plugged, 0); assert.equal(state.usable, 8192);
  assert.match(state.status, /普通缩容不会/);
});
test('pinned aggregate range is skipped and keeps a partial shrink above target', () => {
  let state = block(model.createState(), 7);
  state = step(target(state, 0));
  assert.equal(state.pending.index, 6); assert.equal(state.cells[7].online, true);
  state = process(state);
  assert.equal(model.totals(state).plugged, 512); assert.equal(state.requested, 0);
  assert.match(state.status, /尚未发送 UNPLUG/); invariant(state);
  state = process(block(state, 7));
  assert.equal(model.totals(state).plugged, 0); invariant(state);
});
test('blocking a free or isolated cell cannot introduce inaccessible guest allocations', () => {
  let state = block(model.createState(), 15);
  assert.equal(state.cells[15].blocked, false);
  state = step(target(state, 0)); state = block(state, state.pending.index);
  assert.equal(state.cells[7].blocked, false); invariant(state);
});
test('device BUSY on PLUG leaves both actual and guest memory unchanged', () => {
  let state = step(busy(target(model.createState(), 6144), true));
  assert.match(state.status, /PLUG 返回 BUSY/); assert.equal(state.pending, null);
  assert.deepEqual(model.totals(state), {plugged: 4096, online: 4096, blocked: 0});
  state = process(busy(state, false)); assert.equal(model.totals(state).online, 6144);
});
test('device BUSY on UNPLUG returns isolated pages without altering plugged size', () => {
  let state = step(busy(target(model.createState(), 0), true));
  assert.equal(model.totals(state).online, 3584);
  state = step(state);
  assert.equal(model.totals(state).online, 4096); assert.equal(model.totals(state).plugged, 4096);
  assert.match(state.status, /归还分配器/); invariant(state);
  state = process(busy(state, false)); assert.equal(model.totals(state).plugged, 0);
});
test('raising target after isolation cancels an unnecessary unplug', () => {
  let state = step(target(model.createState(), 0));
  state = target(state, 6144); state = step(state);
  assert.equal(model.totals(state).plugged, 4096); assert.equal(model.totals(state).online, 4096);
  assert.match(state.status, /未发送 UNPLUG/); invariant(state);
  state = process(state); assert.equal(model.totals(state).online, 6144);
});
test('any config notification after isolation cancels the in-flight preparation before rereading', () => {
  for (const value of [0, 512, 8192]) {
    let state = step(target(model.createState(), 0));
    state = target(state, value); state = step(state);
    assert.equal(model.totals(state).plugged, 4096); assert.equal(model.totals(state).online, 4096);
    assert.match(state.status, /重读配置/); invariant(state);
    state = process(state); assert.equal(model.totals(state).plugged, value);
  }
});
test('new target after PLUG acknowledgment preserves accepted pages before adjusting again', () => {
  let state = step(target(model.createState(), 6144));
  state = target(state, 1024); state = step(state);
  assert.equal(model.totals(state).plugged, 4608); assert.equal(model.totals(state).online, 4608);
  state = process(state); assert.equal(model.totals(state).online, 1024); invariant(state);
});
test('every action preserves input states for reliable undo', () => {
  let state = model.createState();
  const actions = [{kind: 'target', value: 6144}, {kind: 'step'}, {kind: 'step'}, {kind: 'block', index: 0}, {kind: 'busy', value: true}, {kind: 'step'}];
  for (const action of actions) {
    const before = JSON.stringify(state), result = model.apply(state, action);
    assert.equal(JSON.stringify(state), before); assert.notEqual(result, state); state = result; invariant(state);
  }
});
test('mixed goals, temporary pins and device responses preserve accounting', () => {
  let state = model.createState(), seed = 20261014;
  const random = limit => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return (seed >>> 7) % limit; };
  for (let i = 0; i < 2400; i++) {
    const action = random(8);
    state = action === 0 ? target(state, random(17) * 512) : action === 1 ? block(state, random(16)) : action === 2 ? busy(state, Boolean(random(2))) : step(state);
    invariant(state);
  }
  state = busy(state, false);
  state.cells.forEach((cell, index) => { if (cell.blocked) state = block(state, index); });
  state = process(target(state, 8192)); assert.equal(model.totals(state).online, 8192);
  state = process(target(state, 0)); assert.equal(model.totals(state).plugged, 0);
});
class Element {
  constructor() { this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {}; this.hidden = true; this.value = ''; this.textContent = ''; this.disabled = false; this.checked = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  fire(name) { this.listeners[name]?.(); }
}
function fixture() {
  const elements = new Map(), lab = new Element();
  const get = name => { if (!elements.has(name)) elements.set(name, new Element()); return elements.get(name); };
  lab.querySelector = selector => get(selector.match(/^\[data-virtio-mem-(.*)\]$/)[1]);
  return {lab, get, environment: {document: {createElement: () => new Element()}}};
}
test('mount shows accessible controls and separate capacity counters without autoplay', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  assert.equal(controller.state().tick, 0); assert.equal(f.get('controls').hidden, false); assert.equal(f.get('fallback').hidden, true);
  assert.equal(f.get('grid').children.length, 16); assert.equal(f.get('usable').textContent, '4.25 GiB');
  assert.equal(f.get('grid').children[0].attributes['aria-pressed'], 'false');
  f.get('grid').children[0].fire('click'); assert.equal(controller.state().cells[0].blocked, true);
  assert.equal(f.get('grid').children[0].attributes['aria-pressed'], 'true');
  f.get('target').value = '6144'; f.get('target').fire('change');
  f.get('step').fire('click'); assert.equal(f.get('plugged').textContent, '4.5 GiB'); assert.equal(f.get('online').textContent, '4 GiB');
  assert.equal(f.get('step').textContent, '下一步 · 客户机上线');
  f.get('step').fire('click'); assert.equal(f.get('online').textContent, '4.5 GiB');
});
test('undo restores target, pins and BUSY controls; reset restores initial state', () => {
  const f = fixture(), controller = model.mount(f.lab, f.environment);
  const initial = JSON.stringify(controller.state());
  f.get('target').value = '0'; f.get('target').fire('change');
  f.get('busy').checked = true; f.get('busy').fire('change');
  f.get('step').fire('click'); f.get('step').fire('click');
  for (let i = 0; i < 4; i++) f.get('back').fire('click');
  assert.equal(JSON.stringify(controller.state()), initial); assert.equal(f.get('target').value, '4096'); assert.equal(f.get('busy').checked, false);
  assert.equal(f.get('back').disabled, true);
  f.get('grid').children[0].fire('click'); f.get('reset').fire('click');
  assert.equal(JSON.stringify(controller.state()), initial); assert.equal(f.get('back').disabled, true);
});
test('article and controls retain no-JS content, unit boundaries and reduced-motion behavior', () => {
  const root = path.join(__dirname, '../virt_report/render');
  const template = fs.readFileSync(path.join(root, 'templates/_virtio_mem_lab.html'), 'utf8');
  assert.match(template, /data-mechanism-view="lab"/); assert.match(template, /data-mechanism-expand hidden/);
  assert.match(template, /role="status" aria-live="polite" aria-atomic="true"/); assert.match(template, /data-virtio-mem-fallback>/);
  assert.match(template, /每格汇总 512 MiB/); assert.match(template, /256 个 2 MiB 设备块/);
  assert.match(template, /每个回收子块都有暂不可迁移页/);
  const css = fs.readFileSync(path.join(root, 'assets/virtio-mem.css'), 'utf8');
  assert.match(css, /prefers-reduced-motion:reduce/); assert.match(css, /focus-visible/); assert.match(css, /minmax\(0,1fr\)/);
  const article = fs.readFileSync(path.join(root, 'templates/_virtio_mem_article.html'), 'utf8');
  assert.match(article, /成功回收一个子块不要求先 offline 整个 Linux memory block/);
  assert.match(article, /UNPLUG_ALL/); assert.match(article, /NACK/); assert.match(article, /RamDiscardManager/);
});
