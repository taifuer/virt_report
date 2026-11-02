'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../virt_report/render/assets/postcopy.js');
const act = (state, kind = 'advance', page = 2) => model.apply(state, {kind, page});
function active() { let state = model.createState(); for (let n = 0; n < 3; n++) state = act(state); return state; }
function finish(state) {
  for (let n = 0; n < 60 && (!model.complete(state) || state.retry); n++) state = act(state);
  assert.equal(model.complete(state), true); assert.equal(state.retry, null); return state;
}
function invariant(state) {
  assert.deepEqual(state.source, [1, 1, 2, 1, 1, 3, 1, 1]);
  assert.ok(state.sent.every(value => value >= 0 && value <= 1), 'every page is sent at most once');
  assert.equal(state.transfers, state.sent.reduce((a, b) => a + b, 0));
  assert.equal(state.transfers, state.installed + (state.packet ? 1 : 0));
  assert.ok(state.log.length <= 4);
  for (let page = 0; page < 8; page++) {
    if (state.phase !== 'precopy' && state.destination[page] === null) {
      assert.ok(state.pending[page] || (state.packet && state.packet.page === page), `missing P${page} must remain pending or in flight`);
    }
    if (state.requested[page]) assert.equal(state.destination[page], null);
    if (state.sent[page]) assert.equal(state.pending[page], false);
    if (model.ready(state) && state.destination[page] !== null) assert.ok(state.destination[page] >= state.source[page]);
  }
  if (state.waiting) { assert.equal(state.destination[state.waiting.page], null); assert.equal(model.ready(state), true); }
  if (state.retry) {
    assert.notEqual(state.destination[state.retry.page], null);
    assert.equal(state.waiting, null); assert.equal(model.ready(state), true);
  }
  if (state.packet) assert.equal(state.destination[state.packet.page], null);
}
test('switch discards stale and untransferred pages before listening and running', () => {
  let state = model.createState(); assert.equal(state.destination[2], 1); assert.equal(state.source[2], 2);
  state = act(state, 'write'); assert.equal(state.rejected, true); assert.equal(state.faults, 0);
  state = act(state); assert.equal(state.phase, 'discard');
  assert.deepEqual(state.destination, [1, 1, null, null, 1, null, null, null]);
  state = act(state, 'background'); assert.equal(state.rejected, true); assert.equal(state.packet, null);
  state = act(state); assert.equal(state.phase, 'listening'); assert.equal(model.ready(state), false);
  state = act(state); assert.equal(state.phase, 'active'); invariant(state);
});
test('actions are pure and reject unknown actions and page addresses', () => {
  let state = model.createState();
  for (const action of model.example) {
    const before = JSON.stringify(state), next = model.apply(state, action);
    assert.equal(JSON.stringify(state), before); assert.notEqual(next, state); state = next;
  }
  assert.throws(() => act(state, 'skip'), RangeError);
  for (const page of [-1, 8, 1.5, NaN, Infinity, null, '', 'no']) assert.throws(() => act(state, 'access', page), RangeError);
});
test('present pages are accessible without faults or remote requests', () => {
  let state = act(active(), 'access', 0); assert.equal(state.faults, 0); assert.equal(state.request, null);
  state = act(state, 'write', 0); assert.equal(state.destination[0], 2); assert.equal(state.source[0], 1);
  assert.equal(state.transfers, 0); invariant(state);
});
test('a missing page blocks only the modeled vCPU and exposes the actual address fields', () => {
  let state = act(active(), 'access', 2);
  assert.deepEqual(state.waiting, {page: 2, write: false}); assert.equal(state.request.stage, 'fault');
  assert.equal(state.lastFault.address, model.HOST_BASE + 2 * 4096 + 32);
  assert.equal(state.lastFault.offset, 0x2000); assert.equal(state.lastFault.flags, 0);
  assert.equal(state.requested[2], false);
  state = act(state, 'write', 0); assert.equal(state.rejected, true); assert.equal(state.destination[0], 1);
  state = act(state); assert.equal(state.request.stage, 'return-path'); assert.equal(state.requested[2], true);
  assert.deepEqual(state.lastRequest, {page: 2, type: 'MIG_RP_MSG_REQ_PAGES_ID', start: 0x2000, length: 4096, id: 'pc.ram', payloadLength: 19});
  state = act(state); assert.equal(state.request.stage, 'queued'); assert.equal(state.destination[2], null);
  invariant(state);
});
test('first request carries RAMBlock id and later requests reuse the channel id', () => {
  let state = act(active(), 'access', 2);
  for (let n = 0; n < 6; n++) state = act(state);
  assert.equal(state.destination[2], 2); assert.equal(state.waiting, null);
  state = act(state, 'write', 5); assert.equal(state.lastFault.flags, 1);
  state = act(state); assert.equal(state.lastRequest.type, 'MIG_RP_MSG_REQ_PAGES');
  assert.equal(state.lastRequest.id, null); assert.equal(state.lastRequest.payloadLength, 12);
  assert.equal(state.lastRequest.start, 0x5000); invariant(state);
});
test('page installation wakes the vCPU without completing its original write', () => {
  let state = act(active(), 'write', 2);
  state = act(act(act(state))); assert.equal(state.packet.stage, 'transit');
  assert.equal(state.pending[2], false); assert.equal(state.destination[2], null); assert.equal(state.waiting.page, 2);
  state = act(state); assert.equal(state.packet.stage, 'buffered'); assert.equal(state.destination[2], null);
  assert.equal(state.waiting.page, 2); assert.equal(state.requested[2], true);
  state = act(state); assert.equal(state.packet, null); assert.equal(state.waiting, null);
  assert.equal(state.requested[2], false); assert.equal(state.destination[2], 2);
  assert.deepEqual(state.retry, {page: 2, write: true});
  assert.equal(state.source[2], 2); assert.match(state.status, /UFFDIO_COPY/); invariant(state);
  state = act(state); assert.equal(state.retry, null); assert.equal(state.destination[2], 3);
  assert.match(state.status, /重新获得调度.*重试.*写入完成/); invariant(state);
});
test('a woken vCPU must retry its original access before another guest operation', () => {
  let state = act(active(), 'write', 2);
  for (let n = 0; n < 5; n++) state = act(state);
  for (const kind of ['access', 'write']) {
    state = act(state, kind, 0); assert.equal(state.rejected, true);
    assert.deepEqual(state.retry, {page: 2, write: true});
    assert.equal(state.destination[0], 1); assert.equal(state.destination[2], 2);
  }
  state = act(state); assert.equal(state.retry, null); assert.equal(state.destination[2], 3);
  state = act(state, 'write', 0); assert.equal(state.rejected, false); assert.equal(state.destination[0], 2);
  invariant(state);
});
test('background transfer can start while a woken vCPU is awaiting scheduling', () => {
  let state = act(active(), 'access', 2);
  for (let n = 0; n < 5; n++) state = act(state);
  assert.deepEqual(state.retry, {page: 2, write: false});
  state = act(state, 'background');
  assert.equal(state.rejected, false); assert.equal(state.packet.page, 3);
  assert.equal(state.packet.stage, 'transit'); assert.notEqual(state.retry, null);
  const packet = JSON.stringify(state.packet);
  state = act(state); assert.equal(state.retry, null); assert.equal(state.destination[2], 2);
  assert.match(state.status, /重新获得调度.*读取到 v2/);
  assert.equal(JSON.stringify(state.packet), packet); invariant(state);
});
test('memory delivery completion does not imply the last faulting guest write has completed', () => {
  let state = active();
  for (let n = 0; n < 12; n++) state = act(state);
  assert.equal(state.destination[7], null);
  assert.equal(state.destination.filter(value => value !== null).length, 7);
  state = act(state, 'write', 7);
  for (let n = 0; n < 5; n++) state = act(state);
  assert.equal(model.complete(state), true); assert.equal(state.destination[7], 1);
  assert.deepEqual(state.retry, {page: 7, write: true}); invariant(state);
  state = act(state); assert.equal(model.complete(state), true);
  assert.equal(state.retry, null); assert.equal(state.destination[7], 2); invariant(state);
});
test('queued requests are preferred over new background scans', () => {
  let state = act(active(), 'access', 7); state = act(act(state));
  assert.equal(state.request.stage, 'queued'); state = act(state, 'background');
  assert.equal(state.packet.page, 7); assert.equal(state.packet.reason, 'requested');
  assert.equal(state.pending[2], true); invariant(state);
});
test('a request cannot jump ahead of bytes already in the single page stream', () => {
  let state = act(active(), 'background'); assert.equal(state.packet.page, 2);
  state = act(state, 'access', 7); state = act(act(state));
  assert.equal(state.request.stage, 'queued');
  state = act(state); assert.equal(state.packet.page, 2); assert.equal(state.packet.stage, 'buffered');
  state = act(state); assert.equal(state.destination[2], 2); assert.equal(state.waiting.page, 7);
  state = act(state); assert.equal(state.packet.page, 7); invariant(state);
});
test('background and request race sends a missing page exactly once', () => {
  let state = act(active(), 'background'); assert.equal(state.packet.page, 2);
  state = act(state, 'write', 2); state = act(act(act(state)));
  assert.equal(state.request, null); assert.match(state.status, /跳过重复请求/);
  assert.equal(state.transfers, 1); assert.equal(state.waiting.page, 2);
  state = act(act(state)); assert.equal(state.destination[2], 2); assert.equal(state.waiting, null);
  assert.deepEqual(state.retry, {page: 2, write: true});
  state = act(state); assert.equal(state.destination[2], 3); assert.equal(state.retry, null);
  state = finish(state); assert.equal(state.destination[2], 3); assert.equal(state.transfers, 5); invariant(state);
});
test('buffered background delivery wakes a fault before its access is retried', () => {
  let state = act(act(active(), 'background')); assert.equal(state.packet.stage, 'buffered');
  state = act(state, 'access', 2);
  for (let n = 0; n < 4; n++) state = act(state);
  assert.equal(state.waiting, null); assert.deepEqual(state.retry, {page: 2, write: false});
  assert.equal(state.destination[2], 2); invariant(state);
  state = finish(state);
  assert.equal(state.faults, 1); assert.equal(state.sent[2], 1); assert.equal(state.destination[2], 2); invariant(state);
});
test('page channel is bounded and cannot create a second simultaneous packet', () => {
  let state = act(active(), 'background'), packet = JSON.stringify(state.packet);
  state = act(state, 'background'); assert.equal(state.rejected, true);
  assert.equal(JSON.stringify(state.packet), packet); assert.equal(state.transfers, 1); invariant(state);
});
test('destination writes survive all later background scans and completed operations', () => {
  let state = finish(active()); assert.equal(state.transfers, 5);
  for (let page = 0; page < 8; page++) state = act(state, 'write', page);
  const values = [...state.destination];
  for (let n = 0; n < 20; n++) state = act(state, n % 2 ? 'background' : 'advance');
  assert.deepEqual(state.destination, values); assert.equal(state.transfers, 5); invariant(state);
});
test('a defensive repeated install cannot overwrite a destination-owned page', () => {
  const state = finish(active()); state.destination[2] = 9;
  state.packet = {page: 2, value: 2, stage: 'buffered', reason: 'background'};
  const next = act(state); assert.equal(next.destination[2], 9); assert.equal(next.packet, null);
  assert.equal(next.rejected, true); assert.match(next.status, /拒绝.*覆盖/);
});
test('the supplied example completes memory delivery and retains the faulting guest write', () => {
  let state = model.createState();
  let retries = 0;
  for (const action of model.example) {
    state = model.apply(state, action); invariant(state);
    assert.equal(state.rejected, false);
    if (state.retry) retries++;
  }
  assert.equal(model.complete(state), true); assert.equal(state.destination[2], 3);
  assert.equal(state.transfers, 5); assert.equal(state.faults, 2);
  assert.equal(state.retry, null); assert.equal(retries, 2);
});
test('mixed deterministic accesses and background steps preserve ownership and delivery invariants', () => {
  let seed = 5219, state = active();
  const random = limit => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return (seed >>> 7) % limit; };
  const kinds = ['advance', 'advance', 'access', 'write', 'background'];
  for (let n = 0; n < 1600; n++) { state = act(state, kinds[random(kinds.length)], random(8)); invariant(state); }
  state = finish(state); assert.equal(state.transfers, 5); invariant(state);
});
test('template includes no-JS explanation, visible state structures and fullscreen hooks', () => {
  const template = fs.readFileSync(path.join(__dirname, '../virt_report/render/templates/_postcopy_lab.html'), 'utf8');
  assert.match(template, /data-mechanism-view="lab"/); assert.match(template, /data-mechanism-expand hidden/);
  assert.match(template, /data-postcopy-fallback>/); assert.match(template, /role="status" aria-live="polite"/);
  assert.match(template, /RAMBlock\.bmap/); assert.match(template, /RAMBlock\.receivedmap/);
  assert.match(template, /data-postcopy-fault-record/); assert.match(template, /data-postcopy-request-record/);
  assert.match(template, /源端/); assert.match(template, /目的端/);
  assert.match(template, /原读写尚待重试/);
  assert.match(template, /实际两条线程路径可以交错/);
  const article = fs.readFileSync(path.join(__dirname, '../virt_report/render/templates/_postcopy_article.html'), 'utf8');
  assert.match(article, /页面安装完成时，原来的读写可以尚未完成/);
});
