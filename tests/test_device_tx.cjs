/* Run with: node --test tests/test_device_tx.cjs */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {normalizeOptions, buildTrace} = require('../virt_report/render/assets/device-tx.js');

test('validates paths and fixes e1000 to its MMIO path', () => {
  assert.deepEqual(normalizeOptions(), {device: 'e1000', notification: 'mmio', queueStart: 0});
  assert.deepEqual(normalizeOptions({device: 'e1000', notification: 'suppressed'}), {device: 'e1000', notification: 'mmio', queueStart: 0});
  assert.throws(() => buildTrace({device: 'vfio'}), RangeError);
  assert.throws(() => buildTrace({notification: 'magic'}), RangeError);
  assert.throws(() => buildTrace({queueStart: 65536}), RangeError);
});

for (const options of [{device: 'e1000'}, ...['ioeventfd', 'mmio', 'suppressed'].map(notification => ({device: 'virtio', notification}))]) {
  test(`${options.device}/${options.notification || 'mmio'}: request and completion follow causal order`, () => {
    const trace = buildTrace(options);
    assert.equal(trace.length, 9);
    assert.deepEqual(trace, buildTrace(options));
    for (const [index, frame] of trace.entries()) {
      assert.equal(frame.index, index);
      assert.ok(!frame.descriptorPrepared || frame.payloadPrepared);
      assert.ok(!frame.published || frame.descriptorPrepared);
      assert.ok(!frame.consumed || frame.published);
      assert.ok(!frame.tapAccepted || frame.consumed);
      assert.ok(!frame.completed || frame.tapAccepted);
      assert.ok(!frame.reclaimed || frame.completed);
      assert.ok(frame.head <= frame.tail);
      assert.ok(frame.nodes.length > 0);
      assert.ok(frame.description.length > 20);
    }
    assert.equal(trace.at(-1).reclaimed, true);
    assert.equal(trace.at(-1).head, 1);
    assert.equal(trace.at(-1).tail, 1);
  });
}

test('e1000 descriptor preparation does not update the MMIO tail', () => {
  const trace = buildTrace({device: 'e1000'});
  assert.equal(trace[2].descriptorPrepared, true);
  assert.equal(trace[2].tail, 0);
  assert.equal(trace[3].tail, 1);
  assert.equal(trace[5].head, 0);
  assert.equal(trace[6].head, 1);
});

test('virtio publication precedes notification and used remains unchanged until completion', () => {
  const trace = buildTrace({device: 'virtio'});
  assert.equal(trace[2].tail, 1);
  assert.equal(trace[2].kickSent, false);
  assert.equal(trace[3].kickSent, true);
  assert.equal(trace[4].consumed, true);
  assert.equal(trace[4].head, 0);
  assert.equal(trace[6].head, 1);
});

test('suppressed kick still publishes and completes the descriptor chain', () => {
  const trace = buildTrace({device: 'virtio', notification: 'suppressed'});
  assert.ok(trace.every(frame => !frame.kickSent));
  assert.deepEqual(trace[3].edges, []);
  assert.equal(trace[2].published, true);
  assert.equal(trace.at(-1).reclaimed, true);
  assert.ok(buildTrace({device: 'virtio', notification: 'mmio'})[3].source.includes('virtio_pci_notify_write'));
  assert.ok(buildTrace({device: 'virtio', notification: 'ioeventfd'})[3].source.includes('ioeventfd'));
});

test('trace snapshots do not leak state across independent runs', () => {
  const first = buildTrace({device: 'virtio'});
  first[2].tail = 99;
  first[3].nodes.push('unexpected');
  const second = buildTrace({device: 'virtio'});
  assert.equal(second[2].tail, 1);
  assert.ok(!second[3].nodes.includes('unexpected'));
});

for (const start of [0, 3, 65535]) {
  test(`split ring starting at ${start}: request count, slot and descriptor id are independent`, () => {
    const trace = buildTrace({device: 'virtio', queueStart: start});
    const slot = start % 4, next = (start + 1) & 0xffff;
    const published = trace[2].queue;
    assert.equal(published.availIndex, next);
    assert.equal(published.usedIndex, start);
    assert.equal(published.available[slot], 0);
    assert.equal(published.descriptors[0].next, 2);
    assert.deepEqual(published.descriptors[0], {addr: '0x1000', len: 12, flags: 1, next: 2});
    assert.deepEqual(published.descriptors[2], {addr: '0x2000', len: 60, flags: 0, next: 0});
    assert.equal(published.descriptors[1], null);
    assert.equal(published.descriptors[3], null);
    assert.equal(published.freeDescriptors, 2);
    assert.equal(published.backendIndex, start);
    assert.equal(trace[4].queue.backendIndex, next);
    assert.equal(trace[4].queue.usedIndex, start);
    assert.deepEqual(trace[6].queue.used[slot], {id: 0, len: 0});
    assert.equal(trace[6].queue.usedIndex, next);
    assert.equal(trace[6].queue.driverUsedIndex, start);
    assert.equal(trace[6].queue.freeDescriptors, 2);
    assert.equal(trace[8].queue.driverUsedIndex, next);
    assert.equal(trace[8].queue.freeDescriptors, 4);
    assert.deepEqual(trace[8].queue.used[slot], {id: 0, len: 0}, 'reclaim does not zero shared slots');
    for (const frame of trace) {
      const q = frame.queue;
      assert.ok(((q.availIndex - q.backendIndex) & 0xffff) <= 1);
      assert.ok(((q.usedIndex - q.driverUsedIndex) & 0xffff) <= 1);
      assert.equal(q.available.filter(value => value !== null).length, Number(frame.published));
      assert.equal(q.used.filter(value => value !== null).length, Number(frame.completed));
    }
  });
}

test('split ring fields are independent between steps and runs', () => {
  const trace = buildTrace({device: 'virtio'});
  trace[2].queue.descriptors[0].next = 3;
  trace[2].queue.available[0] = 3;
  assert.equal(trace[3].queue.descriptors[0].next, 2);
  assert.equal(trace[3].queue.available[0], 0);
  assert.equal(buildTrace({device: 'virtio'})[2].queue.descriptors[0].next, 2);
  assert.equal(buildTrace({device: 'e1000', queueStart: 65535})[2].queue, null);
});
