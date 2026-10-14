'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/vhost-path.js');

test('only implemented backends can be selected', () => {
  assert.equal(model.createState().config.backend, 'kernel');
  assert.throws(() => model.createState({backend: 'dpdk'}), RangeError);
  assert.throws(() => model.createState({backend: '__proto__'}), RangeError);
});

for (const backend of Object.keys(model.BACKENDS)) {
  test(`${backend}: configuration, publication and completion remain ordered`, () => {
    let state = model.createState({backend});
    assert.equal(state.avail, 0);
    assert.equal(state.used, 0);
    for (let index = 1; index <= model.LAST_STEP; index++) {
      const old = JSON.stringify(state);
      const next = model.step(state);
      assert.equal(JSON.stringify(state), old, 'stepping does not mutate the previous frame');
      state = next;
      assert.equal(state.index, index);
      if (state.published) assert.ok(state.configured);
      if (state.kicked) assert.ok(state.published);
      if (state.consumed) assert.ok(state.kicked);
      if (state.submitted) assert.ok(state.consumed);
      if (state.completed) assert.ok(state.submitted);
      if (state.notified) assert.ok(state.completed);
      if (state.reclaimed) assert.ok(state.notified);
      assert.ok(state.used <= state.avail);
      assert.equal(state.socketPacketBytes, 0);
      assert.equal(state.history.length, index + 1);
    }
    assert.equal(state.phase, 'done');
    assert.equal(state.avail, 1);
    assert.equal(state.used, 1);
    assert.deepEqual(model.step(state), state);
  });
  test(`${backend}: backwards navigation restores all state`, () => {
    let state = model.createState({backend});
    const frames = [state];
    for (let i = 0; i < model.LAST_STEP; i++) { state = model.step(state); frames.push(state); }
    for (let i = model.LAST_STEP - 1; i >= 0; i--) {
      state = model.back(state);
      assert.deepEqual(state, frames[i]);
    }
    assert.deepEqual(model.back(state), model.createState({backend}));
  });
}

test('control setup does not publish a packet or change queue indexes', () => {
  let state = model.createState({backend: 'user'});
  for (let i = 0; i < 5; i++) {
    state = model.step(state);
    assert.equal(state.avail, 0);
    assert.equal(state.used, 0);
    assert.equal(state.published, false);
    assert.equal(state.socketPacketBytes, 0);
  }
});

test('vhost-user descriptions distinguish control FD messages from payload', () => {
  let state = model.createState({backend: 'user'});
  for (let i = 0; i < 4; i++) state = model.step(state);
  assert.match(state.detail, /配置句柄/);
  for (let i = 4; i < 8; i++) state = model.step(state);
  assert.match(state.detail, /报文内容不通过控制 socket/);
  assert.equal(model.BACKENDS.user.outlet, '外部后端 → 网络数据面');
});

test('all backends publish the same guest queue indexes at each stage', () => {
  const states = Object.keys(model.BACKENDS).map(backend => model.createState({backend}));
  for (let i = 0; i <= model.LAST_STEP; i++) {
    const queue = states.map(state => ({avail: state.avail, used: state.used, reclaimed: state.reclaimed}));
    assert.deepEqual(queue[0], queue[1]);
    assert.deepEqual(queue[1], queue[2]);
    states.forEach((state, index) => { states[index] = model.step(state); });
  }
});
