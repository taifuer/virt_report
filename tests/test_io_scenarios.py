"""Offline backend catalog assembly, schema failures and browser model parity."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from virt_report.io_scenarios import build_io_scenarios, derive_base_operation


ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "virt_report" / "content" / "flows"
MODEL = ROOT / "virt_report" / "render" / "assets" / "virtio-blk-read-model.js"
VARIANT_FILES = ("qemu-io-variants.json", "spdk-io-variants.json")


@pytest.fixture
def base():
    return json.loads((FLOWS / "virtio-blk-read.json").read_text(encoding="utf-8"))


@pytest.fixture
def variants(tmp_path):
    for name in VARIANT_FILES:
        shutil.copyfile(FLOWS / name, tmp_path / name)
    return tmp_path


def edit_variant(directory, filename, mutate):
    path = directory / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def run_node(program, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required only for browser/Python model parity")
    result = subprocess.run(
        [node, "-e", program, str(MODEL)], input=json.dumps(payload, ensure_ascii=False),
        text=True, capture_output=True, timeout=20, check=True,
    )
    return json.loads(result.stdout)


def test_local_operations_match_browser_model_exactly(base):
    before = deepcopy(base)
    browser = run_node("""
      const fs = require('node:fs');
      const model = require(process.argv[1]);
      const base = JSON.parse(fs.readFileSync(0, 'utf8'));
      process.stdout.write(JSON.stringify({
        read: model.getScenario(base, 'read'), write: model.getScenario(base, 'write')
      }));
    """, base)
    assert derive_base_operation(base, "read") == browser["read"]
    assert derive_base_operation(base, "write") == browser["write"]
    assert base == before


def test_all_five_routes_have_complete_read_and_write_scenarios(base):
    original = deepcopy(base)
    catalog = build_io_scenarios(base, FLOWS)
    assert [route["id"] for route in catalog["routes"]] == [
        "local-blk", "local-scsi", "rbd-blk", "spdk-blk", "spdk-scsi",
    ]
    for route in catalog["routes"]:
        assert route["label"]
        assert set(route["scenarios"]) == {"read", "write"}
        for operation, scene in route["scenarios"].items():
            assert scene["request"]["operation"] == operation
            assert scene["steps"][-1]["changes"]["userReturned"] is True
            assert len(scene["phases"]) == 10
            assert scene["steps"][0]["changes"] == {}
            if route["id"] != "local-blk":
                assert scene["id"] == f'{route["id"]}-{operation}'
                assert scene["request"]["backend"] == route["id"]
                assert scene["presentation"]["notificationMode"] in {"poll", "eventfd"}
    assert base == original
    changed = catalog["routes"][1]["scenarios"]["read"]
    changed["request"]["descriptors"][0]["len"] = 999
    changed["sources"].clear()
    assert build_io_scenarios(base, FLOWS)["routes"][1]["scenarios"]["read"]["sources"]
    assert catalog["routes"][1]["scenarios"]["write"]["sources"]


def test_complete_catalog_validates_and_replays_in_browser_model(base):
    catalog = build_io_scenarios(base, FLOWS)
    result = run_node("""
      const fs = require('node:fs'), assert = require('node:assert/strict');
      const model = require(process.argv[1]);
      const {base, catalog} = JSON.parse(fs.readFileSync(0, 'utf8'));
      const summaries = [];
      for (const route of catalog.routes) {
        for (const operation of ['read', 'write']) {
          const expected = route.scenarios[operation];
          const actual = model.getScenario(base, operation, route.id, catalog);
          assert.deepEqual(actual, expected);
          assert(model.validateScenario(actual));
          const trace = model.buildTrace(actual);
          assert(trace.at(-1).state.userReturned);
          assert(trace.at(-1).state.irqInjected);
          const queue = model.buildQueueTrace(actual);
          assert.equal(queue.length, 8);
          queue.forEach((frame, number) => {
            assert.equal(frame.queueIndex, number);
            assert.deepEqual(frame.state, trace[frame.index].state);
            assert.equal(frame.id, trace[frame.index].id);
            if (number) assert(frame.index > queue[number - 1].index);
          });
          assert(queue[0].state.prepared && !queue[0].state.published);
          assert(queue.at(-1).state.reclaimed && !queue.at(-1).state.userReturned);
          const before = JSON.stringify(expected);
          trace[1].state.freeDescriptors = 100;
          assert.equal(JSON.stringify(expected), before);
          summaries.push({id: actual.id, steps: trace.length,
            used: trace.at(-1).state.usedLength, interface: actual.request.interface || 'virtio-blk'});
        }
      }
      process.stdout.write(JSON.stringify(summaries));
    """, {"base": base, "catalog": catalog})
    assert len(result) == 10
    summaries = {item["id"]: item for item in result}
    assert summaries["local-scsi-read"]["used"] == 4204
    assert summaries["local-scsi-write"]["used"] == 4204
    assert summaries["rbd-blk-read"]["used"] == 4097
    assert summaries["rbd-blk-write"]["used"] == 1


def test_local_scsi_layout_is_not_a_renamed_virtio_blk_scene(base):
    catalog = build_io_scenarios(base, FLOWS)
    route = next(route for route in catalog["routes"] if route["id"] == "local-scsi")
    read, write = route["scenarios"]["read"], route["scenarios"]["write"]
    assert [item["len"] for item in read["request"]["descriptors"]] == [51, 108, 4096]
    assert [item["len"] for item in write["request"]["descriptors"]] == [51, 4096, 108]
    assert write["request"]["descriptors"][1]["flags"] == ["NEXT"]
    assert any("scsi" in ref for step in read["steps"] for ref in step["sourceIds"])
    assert read["request"]["usedLength"] == write["request"]["usedLength"] == 4204


def test_unknown_operation_and_write_base_are_rejected(base):
    with pytest.raises(ValueError, match="requested operation"):
        derive_base_operation(base, "flush")
    with pytest.raises(ValueError, match="base must be READ"):
        derive_base_operation(derive_base_operation(base, "write"), "write")


def test_missing_variant_file_is_not_a_silent_local_fallback(base, tmp_path):
    with pytest.raises(FileNotFoundError):
        build_io_scenarios(base, tmp_path)


@pytest.mark.parametrize(("mutate", "expected"), [
    (lambda p: p.update(unrecognized=True), "variant file schema"),
    (lambda p: p["routes"][0]["common"].update(requset={}), "unknown scenario delta field"),
    (lambda p: p["routes"][0]["operations"].pop("write"), "route requires READ and WRITE"),
    (lambda p: p["routes"][0].update(unknown=True), "route fields"),
    (lambda p: p["routes"][0]["common"].update(stepPatches={"missing": {"title": "bad"}}),
     "unknown step patch target"),
    (lambda p: p["routes"][0]["common"].update(phasePatches={"missing": {"title": "bad"}}),
     "unknown phase patch target"),
    (lambda p: p["routes"][0]["common"].update(sourceMap={"guest-pread": "missing"}),
     "unknown source map reference"),
    (lambda p: p["routes"][0]["common"].update(removeSteps=["missing"]), "unknown removed step"),
    (lambda p: p["routes"][0]["common"].update(insertSteps=[{"after": "missing", "step": {}}]),
     "unknown insertion anchor"),
    (lambda p: p["routes"][0]["common"].update(stepPatches={"ready": {"id": "changed"}}),
     "unknown step patch field"),
    (lambda p: p["routes"][0]["common"].update(assumptions="not an array"), "assumptions"),
    (lambda p: p["routes"][0]["operations"]["read"]["request"].update(backend="spdk-blk"),
     "route request identity"),
    (lambda p: p["routes"][0]["operations"]["read"]["request"].update(usedLength=999999),
     "request used length exceeds"),
])
def test_invalid_delta_fails_closed(base, variants, mutate, expected):
    edit_variant(variants, VARIANT_FILES[0], mutate)
    with pytest.raises(ValueError, match=expected):
        build_io_scenarios(base, variants)


def test_unsafe_source_url_is_rejected_after_evidence_merge(base, variants):
    def mutate(payload):
        evidence = next(iter(payload["routes"][0]["sources"].values()))
        evidence["url"] = "javascript:alert(1)"
    edit_variant(variants, VARIANT_FILES[0], mutate)
    with pytest.raises(ValueError, match="source URL"):
        build_io_scenarios(base, variants)


def test_duplicate_backend_across_files_is_rejected(base, variants):
    edit_variant(variants, VARIANT_FILES[1],
                 lambda payload: payload["routes"][0].update(id="local-scsi"))
    with pytest.raises(ValueError, match="duplicate backend"):
        build_io_scenarios(base, variants)


def test_descriptor_permission_error_is_rejected(base, variants):
    def mutate(payload):
        payload["routes"][0]["operations"]["read"]["request"]["descriptors"][1]["flags"] = ["NEXT"]
    edit_variant(variants, VARIANT_FILES[0], mutate)
    with pytest.raises(ValueError, match="descriptor permissions"):
        build_io_scenarios(base, variants)


def test_every_step_must_highlight_at_least_one_source_path(base, variants):
    def mutate(payload):
        for call_path in payload["routes"][0]["operations"]["read"]["callPaths"]:
            call_path["sourceIds"] = ["guest-pread"]
    edit_variant(variants, VARIANT_FILES[0], mutate)
    with pytest.raises(ValueError, match="no highlighted source path"):
        build_io_scenarios(base, variants)


def test_changes_patch_replaces_instead_of_merging_state(base):
    catalog = build_io_scenarios(base, FLOWS)
    for route in catalog["routes"]:
        if not route["id"].startswith("spdk-"):
            continue
        for scene in route["scenarios"].values():
            stages = {step["id"]: step for step in scene["steps"]}
            assert stages["read-data"]["changes"] == {}
            assert stages["spdk-nvme-poll-completion"]["changes"]["dataReady"] is True
            assert [step["id"] for step in scene["steps"]].index("read-data") < [
                step["id"] for step in scene["steps"]].index("spdk-nvme-poll-completion")
