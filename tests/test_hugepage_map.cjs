'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../virt_report/render/assets/hugepage-map.js');
const act = (state, kind, page = 3) => model.apply(state, {kind, page});
function invariant(state) {
  assert.equal(state.pfns.length, 512); assert.equal(state.copiedBytes, 0);
  state.pfns.forEach((pfn, i) => assert.equal(pfn, model.PFN_BASE + i));
  if (state.logging) assert.equal(state.mode, 'small');
  if (state.mode === 'huge') assert.equal(new Set(state.writable).size, 1);
  assert.ok(state.log.length <= 4);
}
test('configuration validates both independent page sizes and page indices', () => {
  assert.deepEqual(model.normalizeConfig(), {hostPageSize: 2097152, guestPageSize: 4096});
  for (const bad of [0, 1, 512, NaN, Infinity, -4096]) {
    assert.throws(() => model.createState({hostPageSize: bad}), RangeError);
    assert.throws(() => model.createState({guestPageSize: bad}), RangeError);
  }
  for (const bad of ['', ' ', ' 3 ', '3e0', '0x3', null, undefined, true, false, [], [3], {}, -1, 512, 1.5, 'foo']) assert.throws(() => model.pageIndex(bad), RangeError);
  assert.equal(model.pageIndex('511'), 511);
  assert.throws(() => act(model.createState(), 'unknown'), RangeError);
});
test('guest page size does not select the EPT leaf size', () => {
  for (const guestPageSize of [4096, 2097152]) {
    assert.equal(model.createState({guestPageSize}).mode, 'huge');
    assert.equal(model.createState({guestPageSize, hostPageSize: 4096}).mode, 'small');
  }
});
test('2 MiB covers exactly 512 child pages and preserves boundary translations', () => {
  const state = model.createState();
  assert.deepEqual(model.summary(state), {leafCount: 1, lowerTablePages: 0, dirtyCount: 0, writableCount: 512, physicalPages: 512});
  assert.equal(model.pageInfo(state, 0).hpa, 0x80000000);
  assert.equal(model.pageInfo(state, 3).gpa, 0x00403000);
  assert.equal(model.pageInfo(state, 3).hpa, 0x80003000);
  assert.equal(model.pageInfo(state, 511).hpa + 4095, 0x801fffff);
});
test('eager split plus software tracking protects every leaf without copying RAM', () => {
  const before = model.createState(), state = act(before, 'start');
  assert.equal(state.mode, 'small'); assert.equal(state.logging, true);
  assert.equal(model.summary(state).leafCount, 512); assert.equal(model.summary(state).lowerTablePages, 1);
  assert.ok(state.writable.every(value => !value)); assert.ok(state.dirty.every(value => !value));
  assert.deepEqual(state.pfns, before.pfns); assert.deepEqual(state.versions, before.versions);
  assert.equal(before.logging, false); invariant(state);
});
test('first write records one child and restores only its writable bit', () => {
  let state = act(act(model.createState(), 'start'), 'write', 511);
  assert.equal(state.dirty[511], true); assert.equal(state.writable[511], true);
  assert.equal(state.writable[510], false); assert.equal(state.dirty[510], false);
  assert.equal(state.writeFaults, 1); assert.equal(state.recordedWrites, 1);
  state = act(state, 'write', 511);
  assert.equal(state.versions[511], 2); assert.equal(state.writeFaults, 1); assert.equal(state.recordedWrites, 1);
  invariant(state);
});
test('recovery is forbidden while logging and occurs in-place when disabled', () => {
  let state = act(act(model.createState(), 'start'), 'write');
  const versions = [...state.versions], pfns = [...state.pfns];
  state = act(state, 'recover'); assert.equal(state.blocked, true); assert.equal(state.mode, 'small');
  state = act(state, 'stop'); assert.equal(state.logging, false); assert.equal(state.mode, 'huge');
  assert.equal(state.dirty[3], true); assert.ok(state.writable.every(value => !value));
  assert.deepEqual(state.versions, versions); assert.deepEqual(state.pfns, pfns);
  assert.match(state.status, /P0 的 W=0/); invariant(state);
});
test('recovered read-only huge leaf can regain W on subsequent write without re-splitting', () => {
  let state = act(act(act(model.createState(), 'start'), 'write'), 'stop');
  state = act(state, 'write', 129);
  assert.equal(state.mode, 'huge'); assert.ok(state.writable.every(Boolean));
  assert.equal(state.writeFaults, 2); assert.equal(state.dirty[129], false);
  assert.equal(state.versions[129], 1); invariant(state);
});
test('recovery preserves writable permission when the first present child is writable', () => {
  let state = act(act(model.createState(), 'start'), 'write', 0);
  state = act(state, 'stop'); assert.ok(state.writable.every(Boolean));
  const faults = state.writeFaults;
  state = act(state, 'write', 511); assert.equal(state.writeFaults, faults); invariant(state);
});
test('physically contiguous PFNs alone do not permit recovery from host 4 KiB mappings', () => {
  let state = act(model.createState({hostPageSize: 4096, guestPageSize: 2097152}), 'start');
  state = act(state, 'write'); state = act(state, 'stop');
  assert.equal(state.mode, 'small'); assert.equal(state.writable[3], true); assert.equal(state.writable[0], false);
  assert.match(state.status, /PFN 连续/); invariant(state);
});
test('a new logging cycle discards old log markers but preserves content and addresses', () => {
  let state = act(act(act(model.createState(), 'start'), 'write'), 'stop');
  state = act(state, 'start'); assert.ok(state.dirty.every(value => !value));
  assert.equal(state.versions[3], 1); assert.equal(state.recordedWrites, 0); invariant(state);
});
test('deterministic actions never mutate previous states', () => {
  for (const hostPageSize of [4096, 2097152]) {
    let state = model.createState({hostPageSize});
    for (let cycle = 0; cycle < 8; cycle++) for (const action of model.example) {
      const snapshot = JSON.stringify(state), next = model.apply(state, action);
      assert.equal(JSON.stringify(state), snapshot); assert.notEqual(next, state); invariant(next); state = next;
    }
  }
});
test('template provides a static fallback, 512-page selection, and shared fullscreen hooks', () => {
  const template = fs.readFileSync(path.join(__dirname, '../virt_report/render/templates/_hugepage_lab.html'), 'utf8');
  assert.match(template, /data-mechanism-view="lab"/); assert.match(template, /data-mechanism-expand hidden/);
  assert.match(template, /data-hugepage-fallback/); assert.match(template, /min="0" max="511" step="1"/);
  assert.match(template, /role="status" aria-live="polite"/);
  assert.match(template, /dirty 表示逐页日志/); assert.match(template, /未启用 initially-all-set/);
});

class Element {
  constructor(tag = '') {
    this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.hidden = true; this.value = ''; this.textContent = ''; this.validationMessage = ''; this.reported = false;
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setCustomValidity(message) { this.validationMessage = message; }
  reportValidity() { this.reported = true; }
  fire(name) { this.listeners[name]?.(); }
}
function fixture() {
  const nodes = new Map(), lab = new Element();
  const get = name => { if (!nodes.has(name)) nodes.set(name, new Element()); return nodes.get(name); };
  lab.querySelector = selector => get(selector.match(/^\[data-hugepage-(.*)\]$/)[1]);
  get('host').value = '2097152'; get('guest').value = '4096'; get('page').value = '3';
  const controller = model.mount(lab, {document: {createElement: tag => new Element(tag)}});
  return {get, controller};
}
test('mount reveals the interactive scene and paints all 512 split leaves', () => {
  const {get, controller} = fixture();
  assert.equal(get('controls').hidden, false); assert.equal(get('scene').hidden, false);
  assert.equal(get('fallback').hidden, true); assert.equal(get('map').dataset.mode, 'huge');
  get('start').fire('click'); assert.equal(get('map').children.length, 512);
  get('page').value = '511'; get('page').fire('change'); get('write').fire('click');
  assert.equal(controller.state().dirty[511], true);
  assert.equal(get('map').children[511].dataset.selected, 'true');
  get('stop').fire('click'); assert.equal(get('map').dataset.mode, 'huge');
});
test('invalid page input blocks mutations and reset restores valid page and details', () => {
  const {get, controller} = fixture();
  get('page').value = '511'; get('page').fire('change');
  get('page').value = '512'; get('write').fire('click');
  assert.equal(controller.state().tick, 0); assert.match(get('page').validationMessage, /0 到 511/);
  assert.equal(get('page').reported, true);
  get('reset').fire('click');
  assert.equal(get('page').value, '3'); assert.equal(get('page').validationMessage, '');
  assert.match(get('detail').children[1].textContent, /^P3 /);
  get('write').fire('click'); assert.equal(controller.state().versions[3], 1);
});
test('scenario changes clear invalid selection and the example remains repeatable', () => {
  const {get, controller} = fixture();
  get('page').value = ''; get('page').fire('change');
  get('host').value = '4096'; get('host').fire('change');
  assert.equal(get('page').value, '3'); assert.equal(get('page').validationMessage, '');
  assert.equal(get('map').children.length, 512);
  for (let i = 0; i < model.example.length; i++) get('demo').fire('click');
  assert.equal(controller.state().logging, false); assert.equal(controller.state().mode, 'small');
  get('demo').fire('click'); assert.equal(controller.state().tick, 1); assert.equal(controller.state().logging, true);
});
