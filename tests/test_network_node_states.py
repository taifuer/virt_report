"""Overview node status follows the same explicit events as the queue drawing."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from virt_report.execution_demos import validate_demo
from virt_report.network_queue import enrich_network_demo


ROOT = Path(__file__).parents[1]
SCENARIOS = (
    "qemu-tx", "qemu-rx", "vhost-tx", "vhost-rx", "dpdk-tx", "dpdk-rx",
    "vdpa-tx", "vdpa-rx", "qemu-tx-backpressure", "qemu-rx-no-buffer",
)


@pytest.fixture(scope="module")
def scenes():
    demo = json.loads((ROOT / "virt_report/content/flows/network-io.json").read_text())
    enrich_network_demo(demo)
    validate_demo(demo)
    return {scene["id"]: scene for scene in demo["scenarios"]}


def replay(scene, order=None):
    nodes = {node["id"]: node["initial"] for node in scene["nodes"]}
    result = {}
    steps = {step["id"]: step for step in scene["steps"]}
    for identifier in order or steps:
        step = steps[identifier]
        nodes.update(step["nodeStates"])
        result[step["id"]] = deepcopy(nodes)
    return result


@pytest.mark.parametrize("identifier", SCENARIOS)
def test_initial_overview_is_specific_and_neutral(scenes, identifier):
    scene = scenes[identifier]
    assert {key: scene["queue"][key] for key in (
        "headerLength", "packetLength", "payloadLength",
    )} == {"headerLength": 12, "packetLength": 60, "payloadLength": 18}
    assert all(node["initial"] and node["initial"] != "准备就绪" for node in scene["nodes"])
    initial = scene["steps"][0]
    assert initial["id"] == "initial"
    assert not initial["nodeStates"] and not initial["active"] and not initial["changes"]
    assert "尚未准备" in replay(scene)["initial"]["queue"]


@pytest.mark.parametrize("identifier", SCENARIOS)
def test_node_states_observe_publication_and_reclaim_boundaries(scenes, identifier):
    scene = scenes[identifier]
    frames = replay(scene)
    tx = "-tx" in identifier
    published = "publish" if tx else "replenish" if identifier.endswith("no-buffer") else "publish-buffer"
    consumed = "consume" if tx else "retry" if identifier.endswith("no-buffer") else "take-buffer"
    assert "avail.idx 仍为 0" in frames["fill-avail"]["queue"]
    assert "avail.idx = 1" in frames[published]["queue"]
    assert "后端已取" in frames[consumed]["queue"]
    assert "used.idx 仍为 0" in frames["fill-used"]["queue"]
    assert "used.idx = 1" in frames["used"]["queue"]
    assert "等待驱动回收" in frames["used"]["queue"]
    assert "已发布" in frames["notify"]["guest"]
    for name in ("queue", "backend", "port", "network"):
        assert frames[scene["steps"][-1]["id"]][name] != frames["initial"][name]
    if tx:
        assert "可复用" in frames["reclaim"]["queue"]
        assert "远端" in frames["used"]["network"] and "未确认" in frames["used"]["network"]
        assert frames["used"]["application"] == "已返回 18，可继续运行"
    else:
        assert "NAPI" in frames["receive"]["queue"]
        assert "新缓冲区" in frames["refill-prepare"]["queue"]
        assert "avail.idx = 2" in frames["refill"]["queue"]
        assert "socket" in frames["socket-enqueue"]["queue"]
        assert "引用已释放" in frames["copy-to-user"]["queue"]
        assert "已返回 18" in frames["recvfrom-return"]["application"]


def test_stalled_paths_keep_inflight_and_unavailable_memory_distinct(scenes):
    tx = replay(scenes["qemu-tx-backpressure"])
    assert "暂不可写" in tx["blocked"]["port"]
    assert "仍在途" in tx["blocked"]["queue"]
    assert "used.idx = 0" in tx["blocked"]["queue"]
    assert "接受重试帧" in tx["retry"]["port"]
    assert "等待完成回调" in tx["retry"]["queue"]
    rx = replay(scenes["qemu-rx-no-buffer"])
    assert "不能写入" in rx["no-buffer"]["queue"]
    assert "暂停" in rx["no-buffer"]["port"]
    assert "已取得新 RX 链" in rx["retry"]["queue"]
    assert "可恢复" in rx["used"]["port"]


def test_dpdk_guest_completion_does_not_imply_pmd_or_remote_completion(scenes):
    tx = replay(scenes["dpdk-tx"])
    assert "尚未提交 PMD" in tx["transfer"]["port"]
    assert tx["used"]["port"] == tx["transfer"]["port"]
    assert tx["reclaim"]["port"] == tx["transfer"]["port"]
    assert "等待网卡完成" in tx["pmd"]["port"]
    assert "PMD 可回收" in tx["pmd-cleanup"]["port"]
    assert "远端接收仍未确认" in tx["pmd-cleanup"]["network"]
    rx = replay(scenes["dpdk-rx"])
    assert "mbuf M" in rx["arrive"]["port"]
    assert "mbuf M 已释放" in rx["free-mbuf"]["port"]
    assert "等待驱动回收" in rx["free-mbuf"]["queue"]


def test_overview_progress_is_not_inferred_from_translated_step_prose(scenes):
    demo = json.loads((ROOT / "virt_report/content/flows/network-io.json").read_text())
    for scene in demo["scenarios"]:
        for step in scene["steps"]:
            step["title"] = "Translated title"
            step["description"] = "Translated description"
            step["detail"] = "Translated detail"
    enrich_network_demo(demo)
    for changed in demo["scenarios"]:
        assert replay(changed) == replay(scenes[changed["id"]])


def test_actual_browser_model_replays_and_resets_network_node_state(scenes):
    script = """
const fs = require('node:fs');
const model = require('./virt_report/render/assets/execution-model.js');
const scenes = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = {};
for (const scene of Object.values(scenes)) {
  const first = model.buildTrace(scene);
  const replay = model.buildTrace(scene);
  result[scene.id] = {
    nodes: Object.fromEntries(first.map(frame => [frame.id, frame.nodes])),
    reset: replay[0].nodes,
    independent: first[0].nodes !== first.at(-1).nodes,
  };
}
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, input=json.dumps(scenes),
        text=True, capture_output=True, check=True,
    )
    for identifier, actual in json.loads(result.stdout).items():
        expected = replay(scenes[identifier], actual["nodes"])
        assert actual["nodes"] == expected
        assert actual["reset"] == expected["initial"]
        assert actual["independent"]
