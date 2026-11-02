"""All disk routes use the same typed queue view without inventing state."""
import json
import subprocess

import pytest

from virt_report.mechanisms import get_io_scenarios


@pytest.mark.parametrize("backend", ["local-blk", "local-scsi", "rbd-blk", "spdk-blk", "spdk-scsi"])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_disk_queue_snapshots_preserve_layout_and_publication(backend, operation):
    scene = next(route for route in get_io_scenarios()["routes"]
                 if route["id"] == backend)["scenarios"][operation]
    result = subprocess.run(["node", "-e", r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const model = require('./virt_report/render/assets/virtio-blk-read-model.js');
const scene = JSON.parse(fs.readFileSync(0, 'utf8'));
const frames = model.buildTrace(scene), original = JSON.stringify({scene, frames});
const snapshots = frames.map(frame => model.buildQueueSnapshot(scene, frame));
assert.equal(JSON.stringify({scene, frames}), original, 'projection must not mutate the trace');
const scsi = scene.request.interface === 'virtio-scsi', writing = scene.request.operation === 'write';
const byFlag = flag => snapshots[frames.findIndex(frame => frame.changes[flag])].state;
for (let index = 0; index < frames.length; index++) {
  const frame = frames[index], {state, meta} = snapshots[index];
  assert.equal(meta.index, scsi ? 2 : 0); assert.equal(meta.size, 8);
  assert.deepEqual(meta.descriptorIndices, [0, 1, 2]);
  assert.equal(state.avail.index, frame.state.availIndex);
  assert.equal(state.avail.consumer, frame.state.backendIndex);
  assert.equal(state.used.index, frame.state.usedIndex);
  assert.equal(state.used.consumer, frame.state.driverIndex);
  assert.equal(state.freeDescriptors, frame.state.freeDescriptors);
  if (!frame.state.prepared) {
    assert.deepEqual(state.descriptors, []); assert.deepEqual(state.buffers, []);
  } else {
    for (let n = 0; n < 3; n++) {
      const desc = state.descriptors[n], original = scene.request.descriptors[n];
      assert.equal(desc.addr, Number(original.addr)); assert.equal(desc.len, original.len);
      assert.deepEqual(desc.flags, original.flags); assert.equal(desc.next, original.next);
    }
    const payload = state.buffers.find(buffer => buffer.id === 'payload');
    assert.equal(payload.length, 4096); assert.equal(payload.validBytes, frame.state.bufferReady ? 4096 : 0);
    assert.equal(payload.writable, !writing);
    const response = state.buffers.find(buffer => buffer.id === 'response');
    assert.equal(response.length, scsi ? 108 : 1);
    assert.equal(response.validBytes, frame.state.statusWritten ? (scsi ? 12 : 1) : 0);
    assert(response.writable);
  }
  assert.equal(state.avail.slots[0], frame.state.published ? 0 : null);
  assert.deepEqual(state.used.slots[0], frame.state.usedWritten ? {id:0,len:frame.state.usedLength} : null);
  assert.equal(state.response.ready, frame.state.statusWritten);
}
const prepared = byFlag('prepared'), published = byFlag('published');
assert.equal(prepared.avail.index, 0); assert.equal(prepared.avail.slots[0], null);
assert.equal(prepared.freeDescriptors, 5); assert.equal(prepared.ownership, 'driver');
assert.equal(published.avail.index, 1); assert.equal(published.ownership, 'device');
const filled = byFlag('usedWritten'), completed = byFlag('usedPublished'), reclaimed = byFlag('reclaimed');
assert.equal(filled.used.index, 0); assert(filled.used.slots[0]);
assert.equal(completed.used.index, 1); assert.equal(completed.used.consumer, 0);
assert(completed.buffers.every(buffer => buffer.state === 'completed'));
assert.equal(reclaimed.used.consumer, 1); assert.equal(reclaimed.freeDescriptors, 8);
assert.equal(reclaimed.avail.slots[0], 0); assert.deepEqual(reclaimed.used.slots[0], filled.used.slots[0]);
assert(reclaimed.descriptors.every(desc => desc.state === 'reclaimed'));
assert(reclaimed.buffers.every(buffer => buffer.state.includes('缓冲区仍需保持有效')));
if (scsi) {
  assert.equal(prepared.descriptors[writing ? 2 : 1].len, 108);
  assert.equal(completed.response.senseLength, 0);
  const expected = scene.request.backend === 'local-scsi' || !writing ? 4204 : 108;
  assert.equal(filled.used.slots[0].len, expected);
} else assert.equal(filled.used.slots[0].len, writing ? 1 : 4097);
snapshots.at(-1).state.descriptors[0].flags.push('test');
assert(!prepared.descriptors[0].flags.includes('test'), 'snapshots must not share mutable arrays');
console.log('ok');
"""], input=json.dumps(scene), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("backend,mode", [("local-blk", "flush"), ("local-scsi", "fua")])
def test_disk_queue_completion_contracts(backend, mode):
    scene = next(route for route in get_io_scenarios()["routes"]
                 if route["id"] == backend)["scenarios"]["write"]
    result = subprocess.run(["node", "-e", r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const model = require('./virt_report/render/assets/virtio-blk-read-model.js');
const contracts = require('./virt_report/render/assets/disk-completion-model.js');
const {scene,mode} = JSON.parse(fs.readFileSync(0,'utf8'));
const frames = contracts.extend(scene,model.buildTrace(scene),mode).frames;
for (const frame of frames) {
  const {state,meta} = model.buildQueueSnapshot(scene,frame);
  if (frame.queueState) {
    assert.equal(state,frame.queueState, 'F1 must reuse its explicit second-generation state');
    assert.equal(meta.requestId,'F1'); assert.deepEqual(meta.descriptorIndices,[0,1]);
  } else {
    assert.equal(meta.requestId,'R1'); assert.equal(state.freeDescriptors,frame.state.freeDescriptors);
  }
}
if (mode === 'flush') {
  const last = model.buildQueueSnapshot(scene,frames.at(-1));
  assert.equal(last.state.avail.index,2); assert.equal(last.state.used.index,2);
  assert.deepEqual(last.state.used.slots.slice(0,2),[{id:0,len:1},{id:0,len:1}]);
} else {
  const frame = frames.find(frame => frame.id === 'fua-cache');
  const snapshot = model.buildQueueSnapshot(scene,frame);
  assert(!snapshot.state.response.ready); assert.equal(snapshot.state.used.index,0);
  assert(snapshot.state.buffers.find(buffer => buffer.id === 'payload').validBytes === 4096);
}
"""], input=json.dumps({"scene": scene, "mode": mode}), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
