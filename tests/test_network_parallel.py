"""Network ownership boundaries allow independent completion and forwarding."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from virt_report.execution_demos import validate_demo
from virt_report.network_queue import enrich_network_demo


ROOT = Path(__file__).parents[1]
FORKS = {
    "qemu-tx": ("transfer", ["egress"]),
    "vhost-tx": ("transfer", ["egress"]),
    "qemu-tx-backpressure": ("retry", ["egress"]),
    "dpdk-tx": ("route-interrupt", ["pmd", "pmd-cleanup"]),
    "dpdk-rx": ("route-interrupt", ["free-mbuf"]),
}


@pytest.fixture(scope="module")
def scenes():
    demo = json.loads((ROOT / "virt_report/content/flows/network-io.json").read_text())
    enrich_network_demo(demo)
    validate_demo(demo)
    return {scene["id"]: scene for scene in demo["scenarios"]}


def steps_by_id(scene):
    return {step["id"]: step for step in scene["steps"]}


@pytest.mark.parametrize("identifier", FORKS)
def test_parallel_branches_cover_the_suffix_without_shared_state_writers(scenes, identifier):
    scene = scenes[identifier]
    parallel = scene["parallel"]
    after, host_steps = FORKS[identifier]
    assert parallel["after"] == after
    assert parallel["branches"][1]["steps"] == host_steps
    steps = steps_by_id(scene)
    ids = list(steps)
    suffix = ids[ids.index(after) + 1:]
    branches = parallel["branches"]
    assert set(suffix) == set(branches[0]["steps"]) | set(branches[1]["steps"])
    assert not set(branches[0]["steps"]) & set(branches[1]["steps"])
    for key in ("changes", "nodeStates"):
        writes = [{field for event in branch["steps"] for field in steps[event][key]}
                  for branch in branches]
        assert not writes[0] & writes[1], (identifier, key, writes)
    for branch in branches:
        assert branch["steps"] == [event for event in suffix if event in branch["steps"]]


@pytest.mark.parametrize("direction", ("tx", "rx"))
def test_dpdk_interface_returns_before_the_mbuf_branch(scenes, direction):
    scene = scenes[f"dpdk-{direction}"]
    steps = steps_by_id(scene)
    routed = steps[scene["parallel"]["after"]]
    api = "dequeue" if direction == "tx" else "enqueue"
    assert f"rte_vhost_{api}_burst() 返回 1" in routed["detail"]
    assert f"{api} 已返回 1" in routed["changes"]["backendState"]
    assert "eventfd 写调用内完成" in routed["detail"]
    assert scene["parallel"]["branches"][0]["steps"][0] == "prepare-interrupt"
    if direction == "tx":
        assert "尚未返回调用者" in steps["transfer"]["changes"]["mbufOwner"]
        assert routed["changes"]["mbufOwner"] == "DPDK 应用持有 M"
        assert "dequeue 返回后" in steps["pmd"]["description"]
    else:
        assert "同步 enqueue 返回 1" in steps["free-mbuf"]["description"]
        assert not steps["free-mbuf"]["changes"].keys() & {"guestBufferOwner", "reclaimedIdx"}


def test_vdpa_completion_states_only_the_hardware_buffer_contract(scenes):
    scene = scenes["vdpa-tx"]
    steps = steps_by_id(scene)
    assert "parallel" not in scene
    assert "egress" not in steps
    assert all(link["id"] != "egress" for link in scene["links"])
    assert "结束对 Guest 链和缓冲区的使用" in steps["fill-used"]["description"]
    assert "设备私有缓冲区" in steps["fill-used"]["detail"]
    assert "内部发送进度未展开" in steps["transfer"]["changes"]["networkStage"]


def test_browser_model_supports_both_orders_without_future_guest_state(scenes):
    script = """
const fs = require('node:fs');
const model = require('./virt_report/render/assets/execution-model.js');
const scenes = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = {};
for (const scene of Object.values(scenes).filter(scene => scene.parallel)) {
  result[scene.id] = scene.parallel.branches.map(branch => model.buildTrace(scene, branch.id));
}
process.stdout.write(JSON.stringify(result));
"""
    output = subprocess.run(
        ["node", "-e", script], cwd=ROOT, input=json.dumps(scenes),
        text=True, capture_output=True, check=True,
    )
    for identifier, traces in json.loads(output.stdout).items():
        scene = scenes[identifier]
        steps = steps_by_id(scene)
        for trace in traces:
            fields = {field["key"]: field["initial"]
                      for structure in scene["structures"] for field in structure["fields"]}
            nodes = {node["id"]: node["initial"] for node in scene["nodes"]}
            for frame in trace:
                fields.update(steps[frame["id"]]["changes"])
                nodes.update(steps[frame["id"]]["nodeStates"])
                assert frame["values"] == fields
                assert frame["nodes"] == nodes
        for key in ("values", "nodes"):
            assert traces[0][-1][key] == traces[1][-1][key]
        host_first = {frame["id"]: frame for frame in traces[1]}
        after = scene["parallel"]["after"]
        baseline = deepcopy(host_first[after]["queueState"])
        baseline.pop("changed", None)
        for event in scene["parallel"]["branches"][1]["steps"]:
            queue = deepcopy(host_first[event]["queueState"])
            queue.pop("changed", None)
            assert queue == baseline, (identifier, event)
            assert host_first[event]["values"]["reclaimedIdx"] == "0"
        if identifier == "dpdk-rx":
            assert host_first["free-mbuf"]["values"]["applicationResult"] != "18"
            assert host_first["free-mbuf"]["queueState"]["used"]["consumer"] == 0
