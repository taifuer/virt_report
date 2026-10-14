'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/vfio-dma.js');

test('mapping input has no HPA; HPA is supplied by host page resolution', () => {
  assert.equal(Object.hasOwn(model.MAP, 'hpa'), false);
  const state = model.frame({}, 3);
  assert.equal(state.mapping.hpa, model.HOST_PAGE);
  assert.notEqual(state.mapping.hpa, state.mapping.userVa);
  assert.notEqual(state.mapping.hpa, state.mapping.iova);
});

test('invalid modes and stages are rejected', () => {
  assert.throws(() => model.createState({permission: 'unsafe'}), RangeError);
  assert.throws(() => model.createState({operation: 'execute'}), RangeError);
  assert.throws(() => model.frame({}, -1), RangeError);
  assert.throws(() => model.frame({}, 1.5), RangeError);
  assert.throws(() => model.frame({}, 9), RangeError);
});

for (const permission of ['rw', 'readonly', 'missing']) {
  for (const operation of ['read', 'write']) {
    test(`${permission}/${operation}: binding, attachment, mapping and access are ordered`, () => {
      let state = model.createState({permission, operation});
      const succeeds = permission === 'rw' || (permission === 'readonly' && operation === 'read');
      for (let index = 1; index <= model.LAST_STEP; index++) {
        const before = JSON.stringify(state);
        const next = model.step(state);
        assert.equal(JSON.stringify(state), before, 'previous frames are immutable');
        state = next;
        if (state.attached) assert.ok(state.bound);
        if (state.mapping) assert.ok(state.attached);
        if (state.issued) assert.ok(state.submitted);
        if (state.accessed) {
          assert.ok(state.checked && state.verdict.allowed);
          assert.equal(state.verdict.hpa, 0x21000080);
          assert.equal(state.payloadBytes, 64);
        }
        if (state.completed) assert.ok(state.accessed && !state.fault);
        assert.equal(state.cpuTranslationsPerRequest, 0);
      }
      assert.equal(state.completed, succeeds);
      assert.equal(state.fault, !succeeds);
      assert.equal(state.index, succeeds ? 8 : 6);
      assert.equal(state.memory, succeeds && operation === 'write' ? 'B' : 'A');
      assert.equal(state.deviceData, succeeds && operation === 'read' ? 'A' : null);
      assert.deepEqual(model.step(state), state, 'done and fault are terminal');
    });
  }
}

test('missing mapping fails reads and writes without data movement', () => {
  for (const operation of ['read', 'write']) {
    const state = model.frame({permission: 'missing', operation}, 8);
    assert.equal(state.index, 6);
    assert.equal(state.verdict.reason, 'unmapped');
    assert.equal(state.verdict.hpa, null);
    assert.equal(state.payloadBytes, 0);
    assert.equal(state.memory, 'A');
  }
});

test('read-only mapping accepts device reads but blocks device writes', () => {
  assert.equal(model.frame({permission: 'readonly', operation: 'read'}, 8).completed, true);
  const state = model.frame({permission: 'readonly', operation: 'write'}, 8);
  assert.equal(state.verdict.reason, 'permission');
  assert.equal(state.verdict.hpa, null);
  assert.equal(state.payloadBytes, 0);
  assert.equal(state.memory, 'A');
});

test('bounds checks include the complete request and preserve byte offsets', () => {
  const mapping = model.frame({}, 3).mapping;
  assert.equal(model.checkAccess(mapping, {iova: mapping.iova + 4095, length: 1, operation: 'read'}).hpa, mapping.hpa + 4095);
  for (const request of [
    {iova: mapping.iova - 1, length: 1},
    {iova: mapping.iova + 4095, length: 2},
    {iova: mapping.iova + 4096, length: 1},
    {iova: mapping.iova, length: 0},
    {iova: mapping.iova, length: -1},
    {iova: NaN, length: 4}
  ]) assert.equal(model.checkAccess(mapping, {...request, operation: 'read'}).allowed, false);
});

test('back navigation restores exact state including mapping and memory content', () => {
  for (const permission of ['rw', 'readonly', 'missing']) {
    let state = model.createState({permission, operation: 'write'});
    const frames = [state];
    while (!state.completed && !state.fault) { state = model.step(state); frames.push(state); }
    for (let index = frames.length - 2; index >= 0; index--) {
      state = model.back(state);
      assert.deepEqual(state, frames[index]);
    }
    assert.deepEqual(model.back(state), frames[0]);
  }
});
