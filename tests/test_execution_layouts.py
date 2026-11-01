"""Editorial execution domains remain complete and distinct from shared data."""
import json
import re
from pathlib import Path

import pytest

from virt_report.execution_demos import DEMO_IDS


CONTENT = Path(__file__).resolve().parents[1] / "virt_report" / "content" / "flows"
LAYOUTS = json.loads((CONTENT / "execution-layouts.json").read_text(encoding="utf-8"))
DEMOS = {
    name: json.loads((CONTENT / f"{name}.json").read_text(encoding="utf-8"))
    for name in DEMO_IDS
}
SCENARIOS = [(name, scene) for name, demo in DEMOS.items() for scene in demo["scenarios"]]


def test_layout_catalogue_covers_all_published_execution_scenarios():
    assert set(LAYOUTS) == set(DEMOS)
    for name, demo in DEMOS.items():
        assert set(LAYOUTS[name]) == {scene["id"] for scene in demo["scenarios"]}


@pytest.mark.parametrize("demo_id,scene", SCENARIOS,
                         ids=[f"{name}/{scene['id']}" for name, scene in SCENARIOS])
def test_execution_layout_places_each_node_once_with_readable_domains(demo_id, scene):
    layout = LAYOUTS[demo_id][scene["id"]]
    assert set(layout) <= {"domains", "memory"}
    assert 2 <= len(layout["domains"]) <= 5
    domain_ids = [domain["id"] for domain in layout["domains"]]
    assert len(domain_ids) == len(set(domain_ids))
    placed = []
    for domain in layout["domains"]:
        assert {"id", "title", "context", "nodes"} <= set(domain)
        assert set(domain) <= {"id", "title", "context", "nodes", "span", "inline"}
        assert domain.get("span", 1) in (1, 2)
        assert isinstance(domain.get("inline", False), bool)
        assert re.fullmatch(r"[a-z][a-z0-9-]*", domain["id"])
        assert domain["title"].strip()
        assert re.search(r"[\u4e00-\u9fff]", domain["context"])
        assert domain["nodes"]
        placed.extend(domain["nodes"])
    if memory := layout.get("memory"):
        assert set(memory) == {"title", "description", "nodes"}
        assert memory["title"].strip() and memory["description"].strip()
        assert memory["nodes"]
        placed.extend(memory["nodes"])
    assert sorted(placed) == sorted(node["id"] for node in scene["nodes"])
    assert len(placed) == len(set(placed))


def test_network_guest_queue_is_separate_from_execution_domains():
    for layout in LAYOUTS["network-io"].values():
        assert layout["memory"]["title"] == "Guest RAM"
        assert layout["memory"]["nodes"] == ["queue"]
        assert "queue" not in {node for domain in layout["domains"] for node in domain["nodes"]}
    for scene_id in ("vdpa-tx", "vdpa-rx"):
        hardware = next(domain for domain in LAYOUTS["network-io"][scene_id]["domains"]
                        if domain["id"] == "device")
        assert hardware["nodes"] == ["backend", "port"]
        assert "硬件" in hardware["context"]
    for scene_id in ("dpdk-tx", "dpdk-rx"):
        port = next(domain for domain in LAYOUTS["network-io"][scene_id]["domains"]
                    if domain["id"] == "port")
        assert "用户态" in port["context"] and "物理设备" in port["context"]


def test_memory_and_posted_interrupt_state_are_not_mislabeled_as_guest_queues():
    for scene_id, layout in LAYOUTS["memory-access"].items():
        if scene_id == "mmio-read":
            assert "memory" not in layout
            continue
        assert layout["memory"]["nodes"] == ["ram"]
        assert "Guest RAM" != layout["memory"]["title"]
    dirty = LAYOUTS["memory-access"]["dirty-write-protect"]
    kernel = next(domain for domain in dirty["domains"] if domain["id"] == "kernel")
    assert "bitmap" in kernel["nodes"]
    posted = LAYOUTS["interrupt-delivery"]["apicv-posted"]
    assert posted["memory"]["nodes"] == ["pid", "vapic"]
    assert "宿主内存" in posted["memory"]["description"]


def test_guest_syscall_groups_both_privilege_levels_inside_guest():
    layout = LAYOUTS["cpu-execution"]["guest-syscall"]
    guest = next(domain for domain in layout["domains"] if domain["id"] == "guest")
    assert guest["nodes"] == ["app", "kernel"]
    assert "用户态与内核态" in guest["context"]


@pytest.mark.parametrize("demo_id,scenario_id", [
    ("memory-access", "mmio-read"), ("cpu-execution", "mmio-userspace"),
])
def test_mmio_qemu_nodes_share_a_wider_inline_execution_domain(demo_id, scenario_id):
    layout = LAYOUTS[demo_id][scenario_id]
    qemu = next(domain for domain in layout["domains"] if domain["id"] == "userspace")
    assert qemu["nodes"] == ["qemu", "device"]
    assert qemu["span"] == 2 and qemu["inline"] is True
