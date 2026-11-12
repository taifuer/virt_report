"""Fixed-source boundaries for VFIO remapping and device posted interrupts."""

from pathlib import Path

import pytest

from virt_report.execution_demos import get_demo, validate_demo


LINUX_REVISION = "7d0a66e4bb9081d75c82ec4957c50034cb0ea449"
DEVICE_SCENES = ("vtd-remapped", "vtd-posted")
PACKAGE = Path(__file__).resolve().parents[1] / "virt_report"


def scene(scene_id):
    return next(item for item in get_demo("interrupt-delivery")["scenarios"]
                if item["id"] == scene_id)


def states(item):
    values = {field["key"]: field["initial"]
              for structure in item["structures"] for field in structure["fields"]}
    result = {}
    for step in item["steps"]:
        values.update(step["changes"])
        result[step["id"]] = dict(values)
    return result


def test_device_interrupt_presets_are_separate_from_kvm_software_posting():
    demo = get_demo("interrupt-delivery")
    validate_demo(demo)
    assert scene("apicv-posted")["label"] == "DPDK irqfd + posted interrupt"
    group = next(group for group in demo["selector"]["groups"]
                 if group["label"] == "直通设备")
    assert [item["id"] for item in group["scenarios"]] == list(DEVICE_SCENES)
    assert demo["defaultScenario"] == "msix-software"


def test_device_presets_name_delivery_routes_not_alternative_message_protocols():
    demo = get_demo("interrupt-delivery")
    group = next(group for group in demo["selector"]["groups"]
                 if group["label"] == "直通设备")
    for option, label in zip(group["scenarios"],
                             ("MSI-X · 宿主转发", "MSI-X · VT-d posted"), strict=True):
        item = scene(option["id"])
        assert option["label"] == item["label"] == label
        assert "MSI-X" in item["summary"]
        emit = next(step for step in item["steps"] if step["id"] == "emit")
        assert emit["title"] == "设备发送 MSI-X"
        assert "remappable MSI" in emit["description"]
    remapped = states(scene("vtd-remapped"))
    posted = states(scene("vtd-posted"))
    # Posting changes the route after the message, not the device's emission.
    assert remapped["emit"]["message"] == posted["emit"]["message"]
    assert remapped["emit"]["guest-vector"] == posted["emit"]["guest-vector"] == "0x51"


@pytest.mark.parametrize("scene_id", DEVICE_SCENES)
def test_device_scenes_start_neutral_and_update_real_execution_states(scene_id):
    item = scene(scene_id)
    assert 7 <= len(item["steps"]) <= 9
    first = item["steps"][0]
    assert first["id"] == "ready"
    assert not first["active"] and not first["changes"]
    assert not first["links"] and not first["nodeStates"]
    assert all(node["initial"] and node["initial"] != "准备就绪"
               for node in item["nodes"])
    current = {node["id"]: node["initial"] for node in item["nodes"]}
    for step in item["steps"][1:]:
        assert step["nodeStates"]
        assert set(step["nodeStates"]) & set(step["active"])
        assert any(current[key] != value for key, value in step["nodeStates"].items())
        current.update(step["nodeStates"])
    fields = {field["key"] for structure in item["structures"] for field in structure["fields"]}
    assert "used-idx" not in fields and "last-used" not in fields
    assert "queue" not in {node["id"] for node in item["nodes"]}


def test_remapped_interrupt_keeps_host_and_guest_routing_separate():
    item = scene("vtd-remapped")
    trace = states(item)
    assert list(trace) == ["ready", "emit", "validate", "host", "signal",
                           "pending", "inject", "accept", "eoi"]
    assert trace["emit"]["source-check"] == "待校验"
    assert trace["validate"]["source-check"].startswith("通过")
    assert trace["host"]["eventfd"] == trace["ready"]["eventfd"]
    assert trace["signal"]["eventfd"] == "已触发 irqfd"
    assert trace["signal"]["irr"] == "0"
    assert trace["pending"]["irr"] == "1"
    assert trace["pending"]["isr"] == "0"
    assert trace["inject"]["irr"] == "0" and trace["inject"]["isr"] == "1"
    assert trace["inject"]["handler"] == "未进入"
    assert trace["accept"]["handler"] == "已进入向量 0x51"
    assert trace["eoi"]["isr"] == "0"
    assert {row["host-vector"] for row in trace.values()} == {"0xB1"}
    assert {row["guest-vector"] for row in trace.values()} == {"0x51"}
    assert {row["host-destination"] for row in trace.values()} == {"2"}
    assert {row["guest-destination"] for row in trace.values()} == {"1"}
    assumptions = " ".join(item["assumptions"])
    assert "关闭 APICv" in assumptions and "不使用宿主 Posted MSI" in assumptions
    assert "QEMU 参与配置" in assumptions


def test_device_posted_flow_distinguishes_pir_and_notification_vectors():
    item = scene("vtd-posted")
    trace = states(item)
    assert list(trace) == ["ready", "emit", "validate", "post", "notify",
                           "sync", "accept", "eoi"]
    assert trace["validate"]["pir"] == "0"
    assert trace["post"]["pir"] == "1" and trace["post"]["on"] == "1"
    assert trace["post"]["irr"] == "0"
    assert trace["notify"]["pir"] == "1"
    assert trace["sync"]["pir"] == trace["sync"]["on"] == "0"
    assert trace["sync"]["irr"] == "1" and trace["sync"]["isr"] == "0"
    assert trace["sync"]["handler"] == "未进入"
    assert trace["accept"]["irr"] == "0" and trace["accept"]["isr"] == "1"
    assert trace["eoi"]["isr"] == "0"
    assert {row["guest-vector"] for row in trace.values()} == {"0x51"}
    assert {row["irte-vector"] for row in trace.values()} == {"0x51"}
    assert {row["nv"] for row in trace.values()} == {"POSTED_INTR_VECTOR"}
    assert {row["ndst"] for row in trace.values()} == {"2"}
    assert {row["sn"] for row in trace.values()} == {"0"}
    assert not {"vfio", "kvm"} & {node["id"] for node in item["nodes"]}
    memory = item["layout"]["memory"]
    assert memory["nodes"] == ["pid"] and "宿主内存" in memory["title"]
    assert "不属于 Guest RAM" in memory["description"]


def test_posted_assumptions_and_fallback_are_explicit():
    item = scene("vtd-posted")
    assumptions = " ".join(item["assumptions"])
    for phrase in ("VT-d 支持", "APICv", "irq-bypass", "单一 vCPU", "广播",
                   "NMI", "non-root", "SN = 0", "64 字节对齐", "回退",
                   "被抢占", "阻塞", "迁核", "EOI-exit bitmap"):
        assert phrase in assumptions
    assert {part["stepIds"][0] for part in item["sourceSteps"]} >= {
        "post", "sync", "accept", "eoi",
    }
    assert all(part["kind"] == "instruction" for part in item["sourceSteps"])
    assert all("配置" in path["context"] or "调度" in path["context"]
               for path in item["callPaths"])
    assert "VFIO 宿主 MSI handler" in next(
        step["detail"] for step in item["steps"] if step["id"] == "accept")


def test_posted_scheduling_is_not_taught_as_an_automatic_remapping_fallback():
    item = scene("vtd-posted")
    assumptions = " ".join(item["assumptions"])
    assert "并不因 vCPU 暂时未运行就自动切回普通 IRTE" in assumptions
    assert "不是设备类型或应用逐次 I/O 决定路径" in assumptions
    scheduling = next(path for path in item["callPaths"]
                      if path["id"] == "blocked-or-moved")
    assert scheduling["sourceIds"] == ["pi-put", "pi-load"]
    assert "另一类配置变化" in scheduling["boundary"]
    assert "保留 pending" in scheduling["boundary"]
    eoi = next(step for step in item["steps"] if step["id"] == "eoi")
    assert "不等于自动切回普通 IRTE" in eoi["detail"]
    assert {"pi-put", "pi-load"} <= set(eoi["sourceIds"])
    assert "关闭 APICv 只是本例条件" in " ".join(scene("vtd-remapped")["assumptions"])


@pytest.mark.parametrize("template", ["_interrupt_overview_article.html"])
def test_related_articles_distinguish_msix_route_configuration_and_scheduling(template):
    content = (PACKAGE / "render" / "templates" / template).read_text()
    for concept in ("都使用 MSI-X", "NVMe 控制器或 GPU", "单一 vCPU",
                    "CPU 侧", "暂时未运行不等于自动切回普通 IRTE",
                    "arch/x86/kvm/irq.c#L426-L475",
                    "arch/x86/kvm/vmx/posted_intr.c#L223-L267"):
        assert concept in content


def test_acceleration_diagram_shares_msix_before_the_irte_route_split():
    diagram = (PACKAGE / "content" / "diagrams"
               / "interrupt-acceleration-layers.mmd").read_text()
    assert '两条路径都发送 MSI-X"] --> IOMMU[' in diagram
    assert 'IOMMU -->|普通 remapped IRTE| VFIO[' in diagram
    assert 'VFIO --> EVENT' in diagram
    assert 'IOMMU -->|posted IRTE<br/>IOMMU 写 PIR| PI' in diagram
    assert 'EVENT -->|CPU 侧 posted 可用时<br/>KVM 写 PIR| PI[' in diagram


def test_remapping_sources_are_pinned_to_reviewed_linux_revision():
    sources = get_demo("interrupt-delivery")["sources"]
    for source_id in ("vtd-source", "vtd-msi", "vtd-remap", "vtd-posting",
                      "vfio-msi", "pi-bind", "pi-route", "pi-put", "pi-load"):
        source = sources[source_id]
        assert source["revision"] == LINUX_REVISION
        assert f"/blob/{LINUX_REVISION}/{source['path']}#L" in source["url"]
    assert sources["vfio-msi"]["symbol"] == "vfio_msihandler()"
    assert sources["vtd-posting"]["symbol"] == "intel_ir_set_vcpu_affinity()"
    assert sources["pi-bind"]["symbol"] == "vmx_pi_update_irte()"
    assert sources["pi-route"]["symbol"] == "kvm_pi_update_irte()"
    assert sources["pi-put"]["symbol"] == "vmx_vcpu_pi_put()"
    assert sources["pi-load"]["symbol"] == "vmx_vcpu_pi_load()"
