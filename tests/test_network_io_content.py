"""Semantic regressions for the offline network I/O walkthrough."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from virt_report.execution_demos import validate_demo


CONTENT = Path(__file__).parents[1] / "virt_report" / "content" / "flows" / "network-io.json"
DEMO = json.loads(CONTENT.read_text(encoding="utf-8"))
SCENARIOS = {scene["id"]: scene for scene in DEMO["scenarios"]}
BACKENDS = ("qemu", "vhost", "dpdk", "vdpa")


def snapshot(scene_id: str, step_id: str) -> dict[str, str]:
    """Replay field changes through an inclusive named step."""
    scene = SCENARIOS[scene_id]
    state = {
        field["key"]: field["initial"]
        for structure in scene["structures"]
        for field in structure["fields"]
    }
    for step in scene["steps"]:
        state.update(step["changes"])
        if step["id"] == step_id:
            return state
    raise AssertionError(f"Unknown step {scene_id}/{step_id}")


def position(scene_id: str, step_id: str) -> int:
    return next(
        index for index, step in enumerate(SCENARIOS[scene_id]["steps"])
        if step["id"] == step_id
    )


def test_network_demo_is_valid_and_covers_both_directions_and_backpressure():
    validate_demo(DEMO)
    assert DEMO["defaultScenario"] == "qemu-tx"
    assert set(SCENARIOS) == {
        *(f"{backend}-{direction}" for backend in BACKENDS for direction in ("tx", "rx")),
        "qemu-tx-backpressure",
        "qemu-rx-no-buffer",
    }


@pytest.mark.parametrize("scene_id", SCENARIOS)
def test_network_initial_state_is_neutral_and_steps_are_bounded(scene_id):
    scene = SCENARIOS[scene_id]
    initial = scene["steps"][0]
    assert not initial["active"]
    assert not initial["links"]
    assert not initial["changes"]
    assert initial["focus"] is True
    assert 12 <= len(scene["steps"]) <= 14
    assert len(scene["nodes"]) <= 11
    assert snapshot(scene_id, "initial")["availIdx"] == "0"
    assert snapshot(scene_id, "initial")["usedIdx"] == "0"
    assert any(not step["focus"] for step in scene["steps"])
    structure_ids = [structure["id"] for structure in scene["structures"]]
    assert structure_ids[:2] == ["virtqueue", "buffer"]
    assert structure_ids[-1] == "application"


@pytest.mark.parametrize("backend", BACKENDS)
def test_tx_publication_completion_and_reclaim_are_distinct(backend):
    scene_id = f"{backend}-tx"
    prepared = snapshot(scene_id, "prepare")
    assert prepared["availIdx"] == "0"
    assert "12 B" in prepared["desc"] and "60 B" in prepared["desc"]
    published = snapshot(scene_id, "publish")
    assert published["availIdx"] == "1" and published["usedIdx"] == "0"
    completed = snapshot(scene_id, "used")
    assert completed["usedEntry"] == "slot 0 → { id: 0, len: 0 }"
    assert completed["usedIdx"] == "1" and completed["reclaimedIdx"] == "0"
    assert snapshot(scene_id, "reclaim")["reclaimedIdx"] == "1"
    for step in SCENARIOS[scene_id]["steps"]:
        assert snapshot(scene_id, step["id"])["delivery"] == "远端是否收到：未确认"


@pytest.mark.parametrize("backend", BACKENDS)
def test_rx_publishes_writable_space_before_arrival_and_preserves_old_data(backend):
    scene_id = f"{backend}-rx"
    assert position(scene_id, "publish-buffer") < position(scene_id, "arrive")
    published = snapshot(scene_id, "publish-buffer")
    assert "WRITE" in published["desc"]
    assert published["availIdx"] == "1" and published["usedIdx"] == "0"
    written = snapshot(scene_id, "write-buffer")
    assert "72 B" in written["payload"] and written["usedIdx"] == "0"
    completed = snapshot(scene_id, "used")
    assert completed["usedEntry"] == "slot 0 → { id: 2, len: 72 }"
    assert completed["usedIdx"] == "1"
    refilled = snapshot(scene_id, "refill")
    assert refilled["availIdx"] == "2" and refilled["usedIdx"] == "1"
    assert "新缓冲区" in refilled["guestBufferOwner"]
    assert "旧 P1" in refilled["guestBufferOwner"]


def test_dpdk_separates_guest_buffer_from_mbuf_and_pmd():
    copied = snapshot("dpdk-tx", "transfer")
    assert copied["mbufPayload"] == "P1 · pkt_len = 60 B"
    assert copied["mbufOwner"] == "vhost 库准备 M，尚未返回调用者"
    assert copied["usedIdx"] == "0"
    reclaimed = snapshot("dpdk-tx", "reclaim")
    assert reclaimed["guestBufferOwner"] == "驱动已回收，可复用"
    assert snapshot("dpdk-tx", "pmd")["mbufOwner"] == "PMD 持有 M"
    assert position("dpdk-tx", "used") < position("dpdk-tx", "pmd")
    assert position("dpdk-rx", "used") < position("dpdk-rx", "notify")
    assert position("dpdk-rx", "notify") < position("dpdk-rx", "free-mbuf")
    assert "归还 mempool" in snapshot("dpdk-rx", "free-mbuf")["mbufOwner"]
    assert "vhost-user socket 仅承担控制面" in " ".join(SCENARIOS["dpdk-tx"]["assumptions"])
    publish = next(step for step in SCENARIOS["dpdk-tx"]["steps"] if step["id"] == "publish")
    assert "轮询" in publish["detail"]
    assert "抑制 kick" in publish["detail"]
    assert "本例需要通知" not in publish["detail"]
    for direction in ("tx", "rx"):
        scene = SCENARIOS[f"dpdk-{direction}"]
        setup = next(link for link in scene["links"] if link["id"] == "setup")
        assert setup["kind"] == "control"
        assert all("setup" not in step["links"] for step in scene["steps"])


@pytest.mark.parametrize("direction", ("tx", "rx"))
def test_vdpa_is_explicit_hardware_direct_without_svq(direction):
    scene_id = f"vdpa-{direction}"
    assert "硬件" in SCENARIOS[scene_id]["label"]
    assert "不启用 SVQ" in " ".join(SCENARIOS[scene_id]["assumptions"])
    state = snapshot(scene_id, "initial")
    assert state["svq"] == "关闭；硬件直接访问 Guest virtqueue"
    assert "GPA / IOVA" in state["dmaMap"]
    assert all("control" not in step["active"] for step in SCENARIOS[scene_id]["steps"])


def test_qemu_tx_backpressure_does_not_return_buffer_early():
    state = snapshot("qemu-tx-backpressure", "blocked")
    assert state["availIdx"] == "1" and state["consumedIdx"] == "1"
    assert state["usedIdx"] == "0" and state["reclaimedIdx"] == "0"
    assert "async_tx.elem" in state["backendState"]
    assert "仍由设备持有" in state["guestBufferOwner"]
    assert snapshot("qemu-tx-backpressure", "retry")["usedIdx"] == "0"
    assert snapshot("qemu-tx-backpressure", "used")["usedIdx"] == "1"


def test_qemu_rx_without_buffers_waits_before_memory_write():
    state = snapshot("qemu-rx-no-buffer", "no-buffer")
    assert state["availIdx"] == state["usedIdx"] == state["consumedIdx"] == "0"
    assert state["payload"] == "空，可准备接收"
    assert "暂停读取" in state["networkStage"]
    assert position("qemu-rx-no-buffer", "replenish") < position("qemu-rx-no-buffer", "write-buffer")
    assert snapshot("qemu-rx-no-buffer", "used")["usedIdx"] == "1"


def test_network_sources_pin_code_and_reference_real_step_sources():
    used = {source for scene in SCENARIOS.values() for step in scene["steps"] for source in step["sourceIds"]}
    assert used <= DEMO["sources"].keys()
    for source in DEMO["sources"].values():
        url = urlsplit(source["url"])
        assert url.scheme == "https"
        if url.netloc == "github.com":
            commit = url.path.split("/blob/")[1].split("/")[0]
            assert len(commit) == 40 and all(char in "0123456789abcdef" for char in commit)
            assert url.fragment.startswith("L")


@pytest.mark.parametrize("scene_id", SCENARIOS)
def test_end_to_end_application_steps_are_excluded_from_queue_focus(scene_id):
    scene = SCENARIOS[scene_id]
    assert {"application", "stack", "guest"} <= {node["id"] for node in scene["nodes"]}
    application_steps = {
        "sendto", "udp-output", "driver-submit", "sendto-return",
        "recvfrom-wait", "socket-enqueue", "copy-to-user", "recvfrom-return",
    }
    for step in scene["steps"]:
        if step["id"] in application_steps:
            assert step["focus"] is False
    assert 7 <= sum(step["focus"] for step in scene["steps"]) <= 8
    assert len("virt-report-demo01".encode("ascii")) == 18
    assert "18 B" in " ".join(scene["assumptions"])
    assert "UDP" in " ".join(scene["assumptions"])


@pytest.mark.parametrize("scene_id", [name for name in SCENARIOS if "-tx" in name])
def test_sendto_returns_without_waiting_for_used_or_network_delivery(scene_id):
    assert position(scene_id, "sendto") < position(scene_id, "udp-output")
    assert position(scene_id, "udp-output") < position(scene_id, "prepare")
    assert position(scene_id, "publish") < position(scene_id, "sendto-return")
    assert position(scene_id, "sendto-return") < position(scene_id, "used")
    returned = snapshot(scene_id, "sendto-return")
    assert returned["applicationResult"] == "18"
    assert returned["usedIdx"] == "0"
    assert returned["delivery"] == "远端是否收到：未确认"
    assert "U0" in returned["userBuffer"] and "复用" in returned["userBuffer"]
    scene = SCENARIOS[scene_id]
    return_link = next(link for link in scene["links"] if link["id"] == "syscall-return")
    assert (return_link["from"], return_link["to"]) == ("stack", "application")
    returned_step = next(step for step in scene["steps"] if step["id"] == "sendto-return")
    assert "两条分支" in returned_step["detail"]
    assert "一种调度顺序" in returned_step["detail"]
    assert "后端也可能更早" in " ".join(scene["assumptions"])


@pytest.mark.parametrize("scene_id", [name for name in SCENARIOS if "-rx" in name])
def test_recvfrom_user_copy_is_separate_from_driver_reclaim_and_refill(scene_id):
    assert position(scene_id, "recvfrom-wait") < position(scene_id, "arrive")
    assert position(scene_id, "receive") < position(scene_id, "refill")
    assert position(scene_id, "refill") < position(scene_id, "socket-enqueue")
    assert position(scene_id, "socket-enqueue") < position(scene_id, "copy-to-user")
    assert position(scene_id, "copy-to-user") < position(scene_id, "recvfrom-return")
    queued = snapshot(scene_id, "socket-enqueue")
    assert "1 个 UDP 数据报" in queued["socketBuffers"]
    assert queued["applicationResult"] == "等待可读数据"
    refilled = snapshot(scene_id, "refill")
    assert refilled["availIdx"] == "2" and refilled["usedIdx"] == "1"
    assert refilled["applicationResult"] != "18"
    assert "NAPI" in refilled["guestSkb"]
    assert "接收队列为空" in refilled["socketBuffers"]
    receive = next(step for step in SCENARIOS[scene_id]["steps"] if step["id"] == "receive")
    enqueue = next(step for step in SCENARIOS[scene_id]["steps"] if step["id"] == "socket-enqueue")
    assert "stack-input" not in receive["links"]
    assert "stack-input" in enqueue["links"]
    assert "GRO_NORMAL" in receive["detail"]
    assert "gro_flush_normal" in enqueue["detail"]
    result = snapshot(scene_id, "recvfrom-return")
    assert result["applicationResult"] == "18"
    assert "virt-report-demo01" in result["userBuffer"]
    assert result["availIdx"] == "2" and result["usedIdx"] == "1"
    assert "无需等待 recvfrom 返回" in " ".join(SCENARIOS[scene_id]["assumptions"])


def test_network_layout_covers_application_protocol_and_driver_without_other_domain_edits():
    layouts = json.loads((CONTENT.parent / "execution-layouts.json").read_text(encoding="utf-8"))
    for scene_id, scene in SCENARIOS.items():
        layout = layouts["network-io"][scene_id]
        guest = next(domain for domain in layout["domains"] if domain["id"] == "guest")
        assert guest["nodes"] == ["application", "stack", "guest"]
        assigned = [node for domain in layout["domains"] for node in domain["nodes"]]
        assigned.extend(layout.get("memory", {}).get("nodes", []))
        assert set(assigned) == {node["id"] for node in scene["nodes"]}
        assert len(assigned) == len(set(assigned))
