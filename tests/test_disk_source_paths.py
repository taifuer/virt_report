"""Source relationships describe execution contexts, not an invented global stack."""
from copy import deepcopy
import json
import subprocess

import pytest

from virt_report.disk_source_paths import annotate_disk_source_paths
from virt_report.mechanisms import get_io_scenarios


@pytest.fixture(params=[(backend, operation)
                       for backend in ("local-blk", "local-scsi", "rbd-blk", "spdk-blk", "spdk-scsi")
                       for operation in ("read", "write")])
def scene(request):
    backend, operation = request.param
    return next(route for route in get_io_scenarios()["routes"]
                if route["id"] == backend)["scenarios"][operation]


def test_disk_relations_only_override_adjacent_reviewed_pairs(scene):
    for path in scene["callPaths"]:
        adjacent = set(zip(path["sourceIds"], path["sourceIds"][1:]))
        assert path["flow"] in {"sequence", "return"}
        for relation in path["relations"]:
            assert (relation["from"], relation["to"]) in adjacent
            assert relation["kind"] in {"call", "via"}
        if path["id"] == "application-resume":
            assert path["flow"] == "return"
            assert path["relations"] == []
            assert path["entry"]["kind"] == "return"
        if entry := path.get("entry"):
            assert entry["kind"] in {"notification", "async", "callback", "return"}
            assert entry["label"]


def test_disk_metadata_is_idempotent_and_does_not_alter_trace_or_evidence(scene):
    annotated = deepcopy(scene)
    annotate_disk_source_paths(annotated)
    assert annotated == scene
    assert annotated["steps"] == scene["steps"]
    assert annotated["sources"] == scene["sources"]


def test_local_disk_distinguishes_calls_operations_and_async_contexts():
    scene = get_io_scenarios()["routes"][0]["scenarios"]["read"]
    paths = {path["id"]: path for path in scene["callPaths"]}

    def relation(path, first, second):
        item = paths[path]
        return next((edge["kind"] for edge in item["relations"]
                     if (edge["from"], edge["to"]) == (first, second)), item["flow"])

    assert relation("guest-submission", "guest-pread", "guest-vfs-read") == "call"
    assert relation("guest-submission", "guest-read-iter", "guest-direct-read") == "via"
    assert relation("guest-submission", "ring-publish", "kick-check") == "sequence"
    assert relation("qemu-submission", "queue-pop", "request-parse") == "sequence"
    assert relation("qemu-completion", "used-record", "used-publish") == "sequence"
    assert paths["qemu-submission"]["entry"]["kind"] == "notification"
    assert paths["host-worker-read"]["entry"]["kind"] == "async"
    assert paths["qemu-completion"]["entry"]["kind"] == "callback"


def test_rbd_callback_and_bh_are_not_nested_calls():
    route = next(route for route in get_io_scenarios()["routes"] if route["id"] == "rbd-blk")
    for scene in route["scenarios"].values():
        path = next(path for path in scene["callPaths"] if path["id"] == "rbd-callback-bh")
        assert path["flow"] == "sequence"
        assert not path["relations"]
        assert path["entry"]["kind"] == "callback"


def test_browser_disk_model_rejects_stale_or_invalid_source_metadata():
    scene = get_io_scenarios()["routes"][0]["scenarios"]["read"]
    result = subprocess.run(["node", "-e", r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const model = require('./virt_report/render/assets/virtio-blk-read-model.js');
const scene = JSON.parse(fs.readFileSync(0, 'utf8'));
assert(model.validateScenario(scene));
const mutate = patch => {
  const changed = JSON.parse(JSON.stringify(scene));
  Object.assign(changed.callPaths[0], patch);
  return changed;
};
for (const patch of [
  {flow: 'guessed-call'}, {flow: null}, {relations: null},
  {entry: {kind: 'async', label: ''}}, {entry: {kind: 'call', label: 'incorrect entry'}},
  {relations: [{from: 'guest-pread', to: 'guest-direct-read', kind: 'call'}]},
  {relations: [{from: 'guest-pread', to: 'guest-vfs-read', kind: 'async'}]},
  {relations: [{from: 'guest-pread', to: 'guest-vfs-read', kind: 'call'},
               {from: 'guest-pread', to: 'guest-vfs-read', kind: 'via'}]},
]) assert.throws(() => model.validateScenario(mutate(patch)));
assert(model.validateScenario(mutate({flow:'sequence', relations:[], entry:{kind:'callback',label:'回调进入'}})));
// Specification/reference entries cannot masquerade as implementations.
const reference = mutate({relations:[{from:'guest-pread',to:'guest-vfs-read',kind:'call'}]});
reference.sources['guest-vfs-read'].kind = 'reference';
assert.throws(() => model.validateScenario(reference));
console.log('ok');
"""], input=json.dumps(scene), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
