'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const stream = require('../virt_report/render/assets/migration-stream.js');

class Element {
  constructor() { this.dataset = {}; this.attributes = {}; this.listeners = {}; this.hidden = false; this.disabled = false; this.textContent = ''; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name) { if (!this.disabled && this.listeners[name]) this.listeners[name](); }
}
function fixture(reducedMotion = false) {
  const elements = new Map();
  const get = name => { if (!elements.has(name)) elements.set(name, new Element()); return elements.get(name); };
  const rows = stream.steps.map(step => { const row = new Element(); row.dataset.streamRecord = step.id; row.button = new Element(); row.querySelector = () => row.button; return row; });
  const panels = stream.steps.map(() => new Element());
  const pages = {};
  for (const side of ['source', 'destination']) pages[side] = Array.from({ length: 4 }, () => { const page = new Element(); page.value = new Element(); page.querySelector = () => page.value; return page; });
  const lab = new Element();
  lab.querySelector = selector => get(selector.match(/^\[data-stream-(.*)\]$/)[1]);
  lab.querySelectorAll = selector => selector === '[data-stream-record]' ? rows : selector === '[data-stream-panel]' ? panels : selector === '[data-stream-source-page]' ? pages.source : pages.destination;
  const motion = new Element(); motion.matches = reducedMotion;
  const document = new Element();
  const timers = new Map(), animations = []; let timerId = 0;
  get('packet').animate = (keyframes, options) => { const animation = { keyframes, options, cancelled: false, cancel() { this.cancelled = true; } }; animations.push(animation); return animation; };
  const environment = { matchMedia: () => motion, document, setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id) };
  return { lab, rows, panels, pages, motion, document, environment, timers, animations, button: get, finish: () => animations.at(-1).onfinish(), tick: () => { const calls = [...timers.values()]; timers.clear(); calls.forEach(fn => fn()); } };
}

test('wire markers and concrete example match QEMU 10.2.0 field grammar', () => {
  assert.deepEqual(stream.headerBytes, [0x51, 0x45, 0x56, 0x4d, 0, 0, 0, 3]);
  assert.equal(stream.example.machine.length, 11);
  assert.equal(stream.example.ramBytes, 16 * 1024 * 1024);
  assert.equal(stream.example.pageBytes, 4096);
  assert.equal(stream.example.picInstance, 0x20);
  assert.equal(stream.example.picVersion, 1);
  for (const step of stream.steps.filter(step => step.id.startsWith('ram-'))) { assert.equal(step.ramEOS, true); assert.equal(step.footer, true); }
  for (const step of stream.steps.filter(step => step.layer === 'local')) assert.equal(step.marker, null);
  const command = stream.steps.find(step => step.id === 'switchover');
  assert.equal(command.command, 11); assert.equal(command.payloadBytes, 0); assert.equal(command.footer, undefined);
});
test('static HTML contains visible concrete bytes and continuous full stream without JavaScript', () => {
  const template = fs.readFileSync(path.join(__dirname, '../virt_report/render/templates/_migration_stream_lab.html'), 'utf8');
  const ids = [...template.matchAll(/call panel\('([^']+)'/g)].map(match => match[1]);
  assert.deepEqual(ids, stream.steps.map(step => step.id));
  assert.ok(template.includes('51 45 56 4d | 00 00 00 03'));
  assert.ok(template.includes('data-stream-complete-example'));
  assert.ok(template.includes('00 00 00 00 00 00 10 28'));
  assert.ok(template.includes('00 01 00 00'));
  assert.ok(template.includes('源端 QEMU') && template.includes('目的端 QEMU'));
  assert.ok(!template.includes('stream-viewport'));
  assert.ok(!template.includes('data-stream-panel="{{ key }}" hidden'));
});
test('state is derived deterministically: initial copy, dirty writes, overwrite, final sync', () => {
  assert.deepEqual(stream.stateAt(-1).destinationPages, ['—', '—', '—', '—']);
  assert.deepEqual(stream.stateAt(3).destinationPages, ['A1', 'B1', 'C1', '0']);
  assert.deepEqual(stream.stateAt(3).sourcePages, ['A2', 'B1', 'C2', '0']);
  assert.deepEqual(stream.stateAt(4).destinationPages, ['A2', 'B1', 'C1', '0']);
  assert.deepEqual(stream.stateAt(4).sourcePages, ['A2', 'B2', 'C2', '0']);
  assert.deepEqual(stream.stateAt(7).destinationPages, stream.stateAt(7).sourcePages);
  assert.equal(stream.stateAt(7).cpu, false);
  assert.equal(stream.stateAt(9).cpu, true); assert.equal(stream.stateAt(9).device, false);
  assert.equal(stream.stateAt(10).device, true);
  assert.equal(stream.stateAt(11).eof, true); assert.notEqual(stream.stateAt(11).destination, '运行中');
  for (let i = -1; i < stream.steps.length; i++) assert.ok(!(stream.stateAt(i).source === '运行中' && stream.stateAt(i).destination === '运行中'));
  assert.equal(stream.stateAt(13).destination, '运行中');
});
test('mount is idle; packet actually crosses channel before receiver is updated', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  assert.equal(controller.index(), -1); assert.equal(f.animations.length, 0); assert.equal(f.timers.size, 0);
  f.button('next').fire('click');
  assert.equal(controller.pending(), 0); assert.equal(controller.index(), -1);
  assert.equal(f.lab.dataset.transferring, 'true');
  assert.equal(f.button('parsed').textContent, '尚未读取流头');
  const animation = f.animations[0];
  assert.equal(animation.keyframes[0].left, '0%'); assert.equal(animation.keyframes.at(-1).left, '100%');
  f.finish();
  assert.equal(controller.index(), 0); assert.equal(controller.pending(), null);
  assert.equal(f.button('parsed').textContent, 'magic / version 已校验');
});
test('RAM packet in flight cannot update pages; destination changes only on arrival', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  controller.select(3);
  assert.deepEqual(controller.state().destinationPages, ['—', '—', '—', '—']);
  assert.deepEqual(f.pages.destination.map(page => page.value.textContent), ['—', '—', '—', '—']);
  f.finish();
  assert.deepEqual(f.pages.destination.map(page => page.value.textContent), ['A1', 'B1', 'C1', '0']);
  controller.select(4);
  assert.equal(f.pages.destination[0].value.textContent, 'A1'); f.finish();
  assert.equal(f.pages.destination[0].value.textContent, 'A2');
});
test('reset cancels pending animation and rejects delayed completion callback', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  controller.select(7); const late = f.animations.at(-1).onfinish;
  f.button('reset').fire('click'); late();
  assert.equal(controller.index(), -1);
  assert.deepEqual(controller.state().destinationPages, ['—', '—', '—', '—']);
  assert.equal(f.button('packet').hidden, true); assert.equal(f.animations.at(-1).cancelled, true);
});
test('back and random selection reconstruct earlier state, not cumulative mutations', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  controller.select(10); f.finish(); assert.equal(controller.state().device, true);
  f.button('back').fire('click'); assert.equal(controller.state().device, false);
  controller.select(3); f.finish(); assert.equal(controller.state().cpu, false);
  assert.deepEqual(controller.state().sourcePages, ['A2', 'B1', 'C2', '0']);
  assert.equal(f.panels.filter(panel => !panel.hidden).length, 1);
});
test('playback alternates travel and dwell, then stops at destination restore', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  f.button('play').fire('click');
  for (let i = 0; i < stream.steps.length + 2; i++) { if (controller.pending() !== null) f.finish(); f.tick(); }
  assert.equal(controller.index(), stream.steps.length - 1); assert.equal(f.timers.size, 0);
  assert.equal(f.button('play').attributes['aria-pressed'], 'false');
  assert.equal(f.button('next').disabled, true);
});
test('pause cancels in-flight transfer without applying it', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  f.button('play').fire('click'); const late = f.animations[0].onfinish;
  f.button('play').fire('click'); late();
  assert.equal(controller.index(), -1); assert.equal(controller.pending(), null);
  assert.equal(f.timers.size, 0); assert.equal(f.button('packet').hidden, true);
});
test('local operations emit no moving record; reduced motion keeps manual navigation', () => {
  const f = fixture(true), controller = stream.mount(f.lab, f.environment);
  assert.equal(f.button('play').disabled, true);
  controller.select(5); assert.equal(controller.state().source, '已暂停');
  assert.equal(f.animations.length, 0);
  f.button('next').fire('click'); assert.equal(controller.index(), 6);
  assert.equal(f.animations.length, 0);
  f.motion.matches = false; f.motion.fire('change');
  controller.select(8); assert.equal(controller.index(), 8); assert.equal(f.animations.length, 0);
});
test('hidden document or changed motion preference cancels playback safely', () => {
  const f = fixture(), controller = stream.mount(f.lab, f.environment);
  f.button('play').fire('click'); f.document.hidden = true; f.document.fire('visibilitychange');
  assert.equal(controller.pending(), null); assert.equal(f.timers.size, 0);
  f.document.hidden = false; f.button('play').fire('click');
  f.motion.matches = true; f.motion.fire('change');
  assert.equal(controller.pending(), null); assert.equal(f.button('play').disabled, true);
});
test('mismatched template leaves static example intact', () => {
  const f = fixture(); f.rows[0].dataset.streamRecord = 'old';
  assert.equal(stream.mount(f.lab, f.environment), null); assert.equal(f.lab.dataset.initialized, undefined);
});
