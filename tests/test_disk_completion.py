"""The primary disk trace carries completion contracts without a second player."""
import json
import subprocess

import pytest

from virt_report.mechanisms import get_io_scenarios


@pytest.mark.parametrize("backend,mode", [
    ("local-blk", "ordinary"), ("local-blk", "flush"),
    ("local-scsi", "ordinary"), ("local-scsi", "fua"),
])
def test_disk_contract_keeps_request_identity_and_causal_order(backend, mode):
    scene = next(route for route in get_io_scenarios()["routes"]
                 if route["id"] == backend)["scenarios"]["write"]
    result = subprocess.run(["node", "-e", r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const base = require('./virt_report/render/assets/virtio-blk-read-model.js');
const contracts = require('./virt_report/render/assets/disk-completion-model.js');
const {scene, mode} = JSON.parse(fs.readFileSync(0, 'utf8'));
const original = base.buildTrace(scene);
const originalJSON = JSON.stringify(original);
const frames = contracts.extend(scene, original, mode).frames;
for (const path of scene.callPaths) {
  const adjacent = new Set(path.sourceIds.slice(1).map((id, index) => `${path.sourceIds[index]}:${id}`));
  for (const edge of path.relations || []) assert(adjacent.has(`${edge.from}:${edge.to}`), path.id);
}
assert.equal(JSON.stringify(original), originalJSON, 'base snapshots must not be mutated');
assert.equal(frames[0].state.userReturned, false);
assert.equal(frames.at(-1).state.userReturned, mode !== 'fua');
frames.forEach((frame, index) => {
  assert.equal(frame.index, index);
  for (const id of frame.sourceIds) assert(scene.sources[id], id);
  for (const id of frame.active) assert(['application','driver','queue','notify','device','storage','buffer','completion','interrupt','reclaim'].includes(id));
  assert(scene.phases.some(phase => phase.id === frame.phase));
});
const at = id => frames.findIndex(frame => frame.id === id);
if (mode === 'ordinary') {
  assert.equal(frames.length, original.length);
  assert(frames.every(frame => !frame.durability.stableConfirmed));
} else if (mode === 'flush') {
  const path = scene.callPaths.find(path => path.id === 'flush-contract');
  assert.equal(path.flow, 'sequence');
  assert.equal(path.entry.kind, 'async');
  assert(at('return-to-user') < at('sync-call'));
  assert(at('flush-fill-avail') < at('flush-publish'));
  assert.equal(frames[at('flush-fill-avail')].queueState.avail.index, 1);
  assert.equal(frames[at('flush-publish')].queueState.avail.index, 2);
  assert(frames[at('flush-publish')].queueState.changed.includes('avail.index'));
  assert(at('flush-stable') < at('flush-status'));
  assert(at('flush-fill-used') < at('flush-used'));
  assert.equal(frames[at('flush-fill-used')].queueState.used.index, 1);
  assert.equal(frames[at('flush-used')].queueState.used.index, 2);
  assert(frames[at('flush-used')].queueState.changed.includes('used.index'));
  assert(at('flush-pending') < at('flush-inject'));
  assert(at('flush-inject') < at('flush-handler'));
  const final = frames.at(-1);
  assert.equal(final.id, 'sync-return');
  assert(final.durability.stableConfirmed);
  assert.equal(final.queueState.freeDescriptors, 8);
  assert.equal(final.queueState.used.consumer, 2);
  assert.deepEqual(final.queueState.descriptors.map(d => d.len), [16, 1]);
  assert.deepEqual(final.queueState.used.slots.slice(0, 2), [{id:0,len:1},{id:0,len:1}]);
} else {
  assert.equal(scene.callPaths.find(path => path.id === 'fua-contract').flow, 'sequence');
  assert(!scene.callPaths.some(path => path.id === 'application-resume'));
  assert(at('fua-cache') < at('fua-sync'));
  assert(at('fua-stable') < at('write-status'));
  assert(!frames[at('fua-cache')].state.dataReady);
  assert(frames.at(-1).durability.stableConfirmed);
  assert(!frames.some(frame => frame.followup));
  assert.equal(frames[0].id, 'fua-ready');
  assert.equal(frames.at(-1).id, 'fua-upper-complete');
  assert(frames.at(-1).state.completed);
  assert(scene.assumptions.some(text => text.includes('不模拟应用系统调用')));
  assert(scene.request.command.includes('FUA=1'));
  const submitted = frames[at('submit-read')], finished = frames[at('read-data')];
  assert(submitted.detail.includes('BDRV_REQ_FUA'));
  assert(at('fua-stable') < at('read-data'));
  assert(finished.durability.stableConfirmed);
  assert(finished.description.includes('完成要求的同步'));
  assert(!frames.some(frame => /没有 FUA|未执行 fsync \/ FLUSH \/ FUA/.test(frame.description + frame.detail)));
  assert(finished.sourceIds.includes('sync-file-fua'));
}
assert.throws(() => contracts.extend({...scene, request:{...scene.request,backend:'rbd-blk'}}, original, 'flush'));
console.log('ok');
"""], input=json.dumps({"scene": scene, "mode": mode}), text=True,
        capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
