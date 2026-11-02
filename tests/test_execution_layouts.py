"""Editorial execution domains remain complete and distinct from shared data."""
import json
import re
from pathlib import Path

import pytest

from virt_report.execution_demos import DEMO_IDS, get_demo, validate_execution_layout


CONTENT = Path(__file__).resolve().parents[1] / "virt_report" / "content" / "flows"
LAYOUTS = json.loads((CONTENT / "execution-layouts.json").read_text(encoding="utf-8"))
DEMOS = {
    name: json.loads((CONTENT / f"{name}.json").read_text(encoding="utf-8"))
    for name in DEMO_IDS
}
SCENARIOS = [(name, scene) for name, demo in DEMOS.items() for scene in demo["scenarios"]]
COMPONENTS = {"guest", "kvm", "qemu", "host-kernel", "hardware",
              "host-user", "external", "backend"}
BASE_COMPONENTS = {"guest", "kvm", "qemu", "host-kernel", "hardware"}


def test_layout_catalogue_covers_all_published_execution_scenarios():
    assert set(LAYOUTS) <= set(DEMOS)
    for name, demo in DEMOS.items():
        placed = set(LAYOUTS.get(name, {})) | {scene["id"] for scene in demo["scenarios"] if "layout" in scene}
        assert placed == {scene["id"] for scene in demo["scenarios"]}


@pytest.mark.parametrize("demo_id,scene", SCENARIOS,
                         ids=[f"{name}/{scene['id']}" for name, scene in SCENARIOS])
def test_execution_layout_places_each_node_once_with_readable_domains(demo_id, scene):
    layout = scene.get("layout") or LAYOUTS[demo_id][scene["id"]]
    assert set(layout) <= {"domains", "memory"}
    assert 2 <= len([domain for domain in layout["domains"] if domain["nodes"]]) <= 7
    assert len(layout["domains"]) <= 9
    domain_ids = [domain["id"] for domain in layout["domains"]]
    assert len(domain_ids) == len(set(domain_ids))
    placed = []
    for domain in layout["domains"]:
        assert {"id", "title", "context", "nodes", "component"} <= set(domain)
        assert set(domain) <= {"id", "title", "context", "nodes", "span", "inline",
                               "component", "note"}
        assert domain["component"] in COMPONENTS
        assert domain.get("span", 1) in (1, 2)
        assert isinstance(domain.get("inline", False), bool)
        assert re.fullmatch(r"[a-z][a-z0-9-]*", domain["id"])
        assert domain["title"].strip()
        assert re.search(r"[\u4e00-\u9fff]", domain["context"])
        assert domain["nodes"] or domain.get("note", "").strip()
        placed.extend(domain["nodes"])
    if memory := layout.get("memory"):
        assert set(memory) == {"title", "description", "nodes"}
        assert memory["title"].strip() and memory["description"].strip()
        assert memory["nodes"]
        placed.extend(memory["nodes"])
    assert sorted(placed) == sorted(node["id"] for node in scene["nodes"])
    assert len(placed) == len(set(placed))
    validate_execution_layout(layout, {node["id"] for node in scene["nodes"]})


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
        domains = LAYOUTS["network-io"][scene_id]["domains"]
        port = next(domain for domain in domains if domain["id"] == "port")
        assert "用户态" in port["context"] and "物理设备" in port["context"]
        assert next(domain for domain in domains if domain["component"] == "qemu")["nodes"] == ["control"]
        assert next(domain for domain in domains if domain["component"] == "host-user")["nodes"] == ["backend"]


def test_memory_and_posted_interrupt_state_are_not_mislabeled_as_guest_queues():
    for scene_id, layout in LAYOUTS["memory-access"].items():
        if scene_id.startswith("mmio-"):
            assert "memory" not in layout
            continue
        assert layout["memory"]["nodes"] == ["ram"]
        assert "Guest RAM" != layout["memory"]["title"]
    dirty = LAYOUTS["memory-access"]["dirty-write-protect"]
    kernel = next(domain for domain in dirty["domains"] if domain["id"] == "kernel")
    assert "bitmap" in kernel["nodes"]
    posted = LAYOUTS["interrupt-delivery"]["apicv-posted"]
    assert posted["memory"]["nodes"] == ["queue"]
    assert posted["memory"]["title"] == "Guest RAM"
    host_state = next(domain for domain in posted["domains"] if domain["id"] == "interrupt-state")
    assert host_state["nodes"] == ["pid", "vapic"]
    assert "宿主内存" in host_state["context"]


def test_guest_syscall_groups_both_privilege_levels_inside_guest():
    layout = LAYOUTS["cpu-execution"]["guest-syscall"]
    guest = next(domain for domain in layout["domains"] if domain["id"] == "guest")
    assert guest["nodes"] == ["guest", "kernel"]
    assert "non-root" in guest["context"]
    scene = next(scene for scene in DEMOS["cpu-execution"]["scenarios"] if scene["id"] == "guest-syscall")
    assert scene["presentation"] == "cpu-timeline"
    assert {step["execution"]["lane"] for step in scene["steps"]} == {"guest-user", "guest-kernel"}


@pytest.mark.parametrize("demo_id,scenario_id", [
    ("memory-access", "mmio-read"),
])
def test_mmio_qemu_nodes_share_a_wider_inline_execution_domain(demo_id, scenario_id):
    layout = LAYOUTS[demo_id][scenario_id]
    qemu = next(domain for domain in layout["domains"] if domain["id"] == "userspace")
    assert qemu["nodes"] == ["qemu", "device"]
    assert qemu["span"] == 2 and qemu["inline"] is True


def test_cpu_mmio_timeline_separates_kvm_and_qemu_without_fake_data_handoffs():
    scene = next(scene for scene in DEMOS["cpu-execution"]["scenarios"] if scene["id"] == "mmio-userspace")
    by_id = {step["id"]: step for step in scene["steps"]}
    assert by_id["exit"]["execution"]["lane"] == "kvm"
    assert by_id["request"]["execution"]["lane"] == "qemu"
    assert by_id["complete"]["execution"]["lane"] == "kvm"
    assert by_id["resume"]["execution"]["lane"] == "guest-user"
    assert not scene["links"]


@pytest.mark.parametrize("demo_id,scene", SCENARIOS,
                         ids=[f"{name}/{scene['id']}" for name, scene in SCENARIOS])
def test_base_components_remain_visible_without_inventing_event_nodes(demo_id, scene):
    layout = scene.get("layout") or LAYOUTS[demo_id][scene["id"]]
    assert BASE_COMPONENTS <= {domain["component"] for domain in layout["domains"]}
    background_ids = {domain["id"] for domain in layout["domains"] if not domain["nodes"]}
    assert not background_ids & {node["id"] for node in scene["nodes"]}
    assert not background_ids & {endpoint for link in scene["links"]
                                 for endpoint in (link["from"], link["to"])}
    for domain in layout["domains"]:
        if domain["component"] == "kvm":
            assert "宿主" in domain["context"]


def test_network_guest_privilege_is_explicit_and_not_embedded_in_card_titles():
    for scene in get_demo("network-io")["scenarios"]:
        nodes = {node["id"]: node for node in scene["nodes"]}
        assert nodes["application"]["badge"] == "用户态"
        assert nodes["stack"]["badge"] == nodes["guest"]["badge"] == "内核态"
        assert all("用户态" not in nodes[key]["label"] and "内核态" not in nodes[key]["label"]
                   for key in ("application", "stack", "guest"))


@pytest.mark.parametrize("demo_id", ["cpu-execution", "memory-access", "interrupt-delivery",
                                    "vm-lifecycle", "disk-persistence"])
def test_guest_application_and_driver_cards_expose_explicit_privilege(demo_id):
    for scene in get_demo(demo_id)["scenarios"]:
        guest_ids = {identifier for domain in scene["layout"]["domains"]
                     if domain["component"] == "guest" for identifier in domain["nodes"]}
        # Page tables, pre-kernel firmware, and a generic upper-layer FUA request
        # are not assigned an application/kernel privilege label by guesswork.
        exempt = {"gpt", "firmware"}
        if scene["id"] == "write-fua":
            exempt.add("application")
        for node in scene["nodes"]:
            if node["id"] in guest_ids - exempt:
                assert node["badge"] in {"用户态", "内核态"}


def test_ept_faults_separate_kvm_from_host_memory_management():
    for scene_id in ("ept-missing", "host-page-fault"):
        domains = LAYOUTS["memory-access"][scene_id]["domains"]
        kvm = next(domain for domain in domains if domain["component"] == "kvm")
        host = next(domain for domain in domains if domain["component"] == "host-kernel")
        assert kvm["nodes"] == ["kvm"]
        assert host["nodes"] == ["host-mm"]


def test_interrupt_sender_domains_match_the_fixed_qemu_scenarios():
    for scene_id, sender in (("irqfd", "backend"), ("if-masked", "source")):
        domains = LAYOUTS["interrupt-delivery"][scene_id]["domains"]
        qemu = next(domain for domain in domains if domain["component"] == "qemu")
        assert qemu["nodes"] == [sender]
        assert "QEMU" == qemu["title"]
        assert "宿主用户态" in qemu["context"]


@pytest.mark.parametrize("change,message", [
    (lambda groups: groups[0].update(component="unknown"), "unknown component"),
    (lambda groups: groups.append({"id": "background", "title": "QEMU", "context": "宿主用户态",
                                  "component": "qemu", "nodes": []}), "explanatory note"),
    (lambda groups: groups[1].update(nodes=["guest"]), "exactly once"),
])
def test_layout_validation_rejects_ambiguous_components_or_fabricated_placement(change, message):
    domains = [
        {"id": "guest", "title": "Guest", "context": "客户机", "component": "guest", "nodes": ["guest"]},
        {"id": "kvm", "title": "KVM", "context": "宿主内核", "component": "kvm", "nodes": ["kvm"]},
    ]
    change(domains)
    with pytest.raises(ValueError, match=message):
        validate_execution_layout({"domains": domains}, {"guest", "kvm"})


def test_context_only_domain_is_valid_without_creating_an_execution_node():
    layout = {"domains": [
        {"id": "guest", "title": "Guest", "context": "客户机", "component": "guest", "nodes": ["guest"]},
        {"id": "qemu-background", "title": "QEMU", "context": "宿主用户态", "component": "qemu",
         "nodes": [], "note": "设备配置已经完成，本次不返回用户态。"},
    ]}
    validate_execution_layout(layout, {"guest"})
