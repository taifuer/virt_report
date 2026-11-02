const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../virt_report/render/assets/execution-model.js');
test('phase navigation groups operations without dropping playback or deep-link positions', () => {
  const scene = {nodes: [], structures: [], phases: [
    {id: 'submit', title: '提交请求', start: 'initial'},
    {id: 'process', title: '后端处理', start: 'consume'},
    {id: 'finish', title: '完成回收', start: 'complete'},
  ], steps: ['initial', 'prepare', 'publish', 'consume', 'transfer', 'complete', 'reclaim']
    .map((id, index) => ({id, title: id, changes: {}, nodeStates: {}, focus: index !== 4}))};
  const trace = model.buildTrace(scene);
  assert.deepEqual(model.navigation(scene, trace, 'overview').map(p => [p.index, p.end]),
    [[0, 2], [3, 4], [5, 6]]);
  assert.deepEqual(model.navigation(scene, trace, 'source'), model.phases(scene, trace));
  assert.deepEqual(model.stops(trace, 'overview'), [0, 1, 2, 3, 4, 5, 6]);
  assert.equal(model.next(trace, 'overview', 3, 1), 4);
  assert.equal(model.project(trace, 'source', 4), 4);
  assert.deepEqual(model.navigation(scene, trace, 'structure').map(p => p.index), [0, 1, 2, 3, 5, 6]);
  assert.equal(trace[0].phase, undefined, 'navigation does not mutate snapshots');
});
test('node initial descriptions are neutral and retained until explicitly changed', () => {
  const scene = {nodes: [{id: 'vm', initial: '尚未创建'}, {id: 'manager'}], structures: [],
    steps: [{changes: {}, nodeStates: {}, active: [], focus: true},
      {changes: {}, nodeStates: {vm: '已经创建'}, active: ['vm'], focus: true}]};
  const frames = model.buildTrace(scene);
  assert.deepEqual(frames[0].nodes, {vm: '尚未创建', manager: '准备就绪'});
  assert.deepEqual(frames[0].active, []);
  assert.equal(frames[1].nodes.vm, '已经创建');
  assert.equal(frames[0].nodes.vm, '尚未创建');
});
for (const id of ['network-io', 'memory-access', 'cpu-execution', 'interrupt-delivery',
  'disk-persistence', 'vm-lifecycle', 'virtio-gpu-rendering']) {
  const demo = JSON.parse(fs.readFileSync(path.join(__dirname, `../virt_report/content/flows/${id}.json`), 'utf8'));
  for (const scene of demo.scenarios) test(`${id}/${scene.id}: immutable snapshots and reversible projections`, () => {
    const original = JSON.stringify(scene);
    const frames = model.buildTrace(scene);
    assert.equal(frames.length, scene.steps.length);
    assert.deepEqual(frames[0].changed, []);
    assert.deepEqual(frames[0].active, []);
    let expected = Object.fromEntries(scene.structures.flatMap(group => group.fields.map(f => [f.key, f.initial])));
    frames.forEach((frame, index) => {
      Object.assign(expected, scene.steps[index].changes);
      assert.deepEqual(frame.values, expected);
      assert.deepEqual(frame.changed, Object.keys(scene.steps[index].changes));
      if (index > 0) assert.notEqual(frame.values, frames[index - 1].values);
    });
    for (const view of ['overview', 'structure', 'source']) {
      const stops = model.stops(frames, view);
      assert.equal(stops[0], 0);
      const last = view === 'structure' ? frames.findLast(frame => frame.focus).index : frames.length - 1;
      assert.equal(stops.at(-1), last);
      for (let i = 0; i < stops.length; i++) {
        assert.equal(model.next(frames, view, stops[i], 1), stops[Math.min(stops.length - 1, i + 1)]);
        assert.equal(model.next(frames, view, stops[i], -1), stops[Math.max(0, i - 1)]);
      }
      for (const frame of frames) {
        const target = model.project(frames, view, frame.index);
        assert(stops.includes(target) && target <= frame.index);
        if (stops.includes(frame.index)) assert.equal(target, frame.index);
      }
    }
    frames.at(-1).values.mutated = 'only this snapshot';
    assert(!Object.hasOwn(frames[0].values, 'mutated'));
    assert.equal(JSON.stringify(scene), original);
  });
}

test('network structure view ends at queue recovery/refill, without application steps', () => {
  const demo = JSON.parse(fs.readFileSync(path.join(__dirname,
    '../virt_report/content/flows/network-io.json'), 'utf8'));
  const applicationSteps = new Set(['sendto', 'udp-output', 'driver-submit', 'sendto-return',
    'recvfrom-wait', 'socket-enqueue', 'copy-to-user', 'recvfrom-return']);
  for (const scene of demo.scenarios) {
    const trace = model.buildTrace(scene), stops = model.stops(trace, 'structure');
    const focused = stops.map(index => trace[index]);
    assert(focused.every(frame => frame.focus && !applicationSteps.has(frame.id)), scene.id);
    const last = stops.at(-1);
    assert.equal(trace[last].id, scene.id.includes('-rx') ? 'refill' : 'reclaim', scene.id);
    assert(last < trace.length - 1, scene.id);
    assert.equal(model.next(trace, 'structure', last, 1), last);
    assert.equal(model.project(trace, 'structure', trace.length - 1), last);
    assert.equal(model.stops(trace, 'overview').at(-1), trace.length - 1);
    assert.equal(model.stops(trace, 'source').at(-1), trace.length - 1);
  }
});
