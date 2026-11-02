"""Fixed-source boundaries for VFIO remapping and device posted interrupts."""

import pytest

from virt_report.execution_demos import get_demo, validate_demo


LINUX_REVISION = "7d0a66e4bb9081d75c82ec4957c50034cb0ea449"
DEVICE_SCENES = ("vtd-remapped", "vtd-posted")


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
    assert scene("apicv-posted")["label"] == "APICv · KVM 软件投递"
    group = next(group for group in demo["selector"]["groups"]
                 if group["label"] == "设备直通与 VT-d")
    assert [item["id"] for item in group["scenarios"]] == list(DEVICE_SCENES)
    assert demo["defaultScenario"] == "msix-software"


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


def test_remapping_sources_are_pinned_to_reviewed_linux_revision():
    sources = get_demo("interrupt-delivery")["sources"]
    for source_id in ("vtd-source", "vtd-msi", "vtd-remap", "vtd-posting",
                      "vfio-msi", "pi-bind", "pi-route"):
        source = sources[source_id]
        assert source["revision"] == LINUX_REVISION
        assert f"/blob/{LINUX_REVISION}/{source['path']}#L" in source["url"]
    assert sources["vfio-msi"]["symbol"] == "vfio_msihandler()"
    assert sources["vtd-posting"]["symbol"] == "intel_ir_set_vcpu_affinity()"
    assert sources["pi-bind"]["symbol"] == "vmx_pi_update_irte()"
    assert sources["pi-route"]["symbol"] == "kvm_pi_update_irte()"
