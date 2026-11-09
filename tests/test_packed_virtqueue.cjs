'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createState, advance, position, createSplitState, advanceSplit} = require('../virt_report/render/assets/packed-virtqueue.js');
test('zero flags do not imply an initial completion', () => {
  const initial = createState();
  assert.deepEqual(position(0), {index: 0, wrap: 1});
  assert.deepEqual(advance(initial, 'reclaim'), initial);
  assert.deepEqual(advance(initial, 'complete'), initial);
});
test('publication, completion and reclaim have independent wrap positions', () => {
  let s = createState();
  for (let i = 0; i < 4; i++) s = advance(s, 'publish');
  assert.deepEqual(position(s.published), {index: 0, wrap: 0});
  assert.deepEqual(position(s.completed), {index: 0, wrap: 1});
  assert.deepEqual(advance(s, 'publish'), s);
  assert.deepEqual(s.slots.map(x => x.id), [2,0,3,1]);
  s = advance(s, 'complete');
  assert.equal(s.slots[0].length, 64);
  assert.deepEqual([s.slots[0].avail,s.slots[0].used], [1,1]);
  assert.deepEqual(advance(s, 'publish'), s, 'completion alone does not free a slot');
  s = advance(s, 'reclaim');
  assert.deepEqual([s.slots[0].avail,s.slots[0].used], [1,1], 'reclaim preserves old bits');
  s = advance(s, 'publish');
  assert.deepEqual([s.slots[0].avail,s.slots[0].used], [0,1]);
  for (let i = 0; i < 4; i++) s = advance(s, 'complete');
  assert.deepEqual([s.slots[0].avail,s.slots[0].used], [0,0]);
  for (let i = 0; i < 4; i++) s = advance(s, 'reclaim');
  assert.equal(s.reclaimed, 5);
});
test('successive revolutions preserve ownership and do not mutate snapshots', () => {
  let s = createState();
  const initial = JSON.stringify(s);
  const before = s;
  for (let n = 0; n < 40; n++) {
    s = advance(s, 'publish');
    s = advance(s, 'complete');
    s = advance(s, 'reclaim');
    assert.equal(s.freeIds.length, 4);
    assert.equal(new Set(s.freeIds).size, 4);
  }
  assert.equal(JSON.stringify(before), initial);
  assert.equal(s.published, 40);
  assert.deepEqual(advance(s, 'reset'), createState());
});
test('interleaved actions preserve capacity and buffer ID ownership', () => {
  let s = createState();
  let seed = 12345;
  for (let i = 0; i < 5000; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    s = advance(s, ['publish', 'complete', 'reclaim'][seed % 3]);
    assert.ok(s.reclaimed <= s.completed && s.completed <= s.published);
    assert.ok(s.published - s.reclaimed <= 4);
    assert.equal(s.slots.filter(x => x.state === 'available').length, s.published - s.completed);
    assert.equal(s.slots.filter(x => x.state === 'completed').length, s.completed - s.reclaimed);
    const owned = s.slots.filter(x => x.state !== 'free').map(x => x.id);
    assert.equal(new Set([...owned, ...s.freeIds]).size, 4);
    assert.equal(owned.length + s.freeIds.length, 4);
  }
});
test('Split publishes a descriptor head independently of the ring slot', () => {
  const initial = createSplitState();
  assert.deepEqual(advanceSplit(initial, 'complete'), initial);
  assert.deepEqual(advanceSplit(initial, 'reclaim'), initial);
  let s = advanceSplit(initial, 'publish');
  assert.equal(s.available[0], 2);
  assert.equal(s.descriptors[2].length, 256);
  assert.equal(s.descriptors[0].request, null);
  assert.equal(initial.published, 0);
  s = advanceSplit(s, 'complete');
  assert.deepEqual(s.used[0], {id: 2, length: 64, request: 1});
  assert.equal(s.descriptors[2].length, 256, 'capacity is not overwritten by the completion length');
  const complete = structuredClone(s);
  s = advanceSplit(s, 'reclaim');
  assert.deepEqual(s.used, complete.used, 'reclaim need not clear ring entries');
  assert.deepEqual(s.available, complete.available);
  assert.equal(complete.descriptors[2].state, 'completed', 'snapshots are immutable');
  assert.equal(s.descriptors[2].state, 'free');
  assert.deepEqual(advanceSplit(s, 'reset'), initial);
});
test('Split idx wraps at 65536, not at queue capacity', () => {
  let s = createSplitState();
  for (let n = 0; n < 65537; n++) {
    s = advanceSplit(s, 'publish');
    s = advanceSplit(s, 'complete');
    s = advanceSplit(s, 'reclaim');
    if (n === 3) assert.equal(s.published, 4);
  }
  assert.equal(s.published % 65536, 1);
  assert.equal(s.completed % 65536, 1);
  assert.equal(s.reclaimed % 65536, 1);
  assert.equal(s.used[0].request, 65537);
  assert.equal(s.freeIds.length, 4);
});
test('both formats preserve identical ownership through interleaved actions', () => {
  let packed = createState(), split = createSplitState(), seed = 56789;
  for (let i = 0; i < 5000; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const action = ['publish', 'complete', 'reclaim'][seed % 3];
    packed = advance(packed, action);
    split = advanceSplit(split, action);
    for (const key of ['published', 'completed', 'reclaimed', 'freeIds']) assert.deepEqual(split[key], packed[key]);
    assert.equal(split.descriptors.filter(x => x.state === 'available').length, split.published - split.completed);
    assert.equal(split.descriptors.filter(x => x.state === 'completed').length, split.completed - split.reclaimed);
    const held = split.descriptors.flatMap((desc, index) => desc.state !== 'free' ? [index] : []);
    assert.equal(held.length + split.freeIds.length, 4);
    assert.equal(new Set([...held, ...split.freeIds]).size, 4);
  }
});
