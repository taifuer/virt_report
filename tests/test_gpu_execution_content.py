"""Causal and source-boundary regressions for GPU / mediated GPU walkthroughs."""
import copy
import json
from pathlib import Path

import pytest

from virt_report.config import Config
from virt_report.execution_demos import get_demo, validate_demo, validate_selector
from virt_report.render.render import render_mechanisms_html


CONTENT = Path(__file__).parents[1] / "virt_report/content/flows/gpu-execution.json"
DEMO = json.loads(CONTENT.read_text(encoding="utf-8"))
SCENES = {scene["id"]: scene for scene in DEMO["scenarios"]}


def snapshot(scene_id, step_id):
    """Return the inclusive state of a single, source-reviewed operation."""
    scene = SCENES[scene_id]
    state = {field["key"]: field["initial"]
             for structure in scene["structures"] for field in structure["fields"]}
    for step in scene["steps"]:
        state.update(step["changes"])
        if step["id"] == step_id:
            return state
    raise AssertionError(step_id)


def position(scene_id, step_id):
    return next(i for i, step in enumerate(SCENES[scene_id]["steps"])
                if step["id"] == step_id)


def node_snapshot(scene_id, step_id):
    scene = SCENES[scene_id]
    state = {node["id"]: node.get("initial", "") for node in scene["nodes"]}
    for step in scene["steps"]:
        state.update(step["nodeStates"])
        if step["id"] == step_id:
            return state
    raise AssertionError(step_id)


def test_gpu_demo_is_registered_and_has_only_verified_device_combinations():
    validate_demo(DEMO)
    validate_selector(DEMO["selector"], set(SCENES))
    assert set(SCENES) == {"vfio-upload", "vfio-readback", "mdev-copy"}
    assert get_demo("gpu-execution")["defaultScenario"] == "vfio-upload"
    assert len(DEMO["selector"]["categories"]) == 2


@pytest.mark.parametrize("scene_id", SCENES)
def test_gpu_sources_and_callpaths_are_fixed_and_explicit(scene_id):
    scene = SCENES[scene_id]
    represented = {node for group in scene["layout"]["domains"]
                   for node in group["nodes"]}
    represented.update(scene["layout"]["memory"]["nodes"])
    assert represented == {node["id"] for node in scene["nodes"]}
    assert scene["callPaths"]
    for path in scene["callPaths"]:
        assert path["context"] and path["boundary"] and path["sourceIds"]
        for source_id in path["sourceIds"]:
            source = DEMO["sources"][source_id]
            assert source["symbol"] and source["path"] and source["line"] > 0
            assert len(source["revision"]) == 40
            assert source["revision"] in source["url"]
            assert source["url"].endswith(f'#L{source["line"]}')
    assert all(step["sourceIds"] for step in scene["steps"])


@pytest.mark.parametrize("scene_id", ["vfio-upload", "vfio-readback"])
def test_submission_data_fence_and_application_completion_are_not_conflated(scene_id):
    assert position(scene_id, "submit-return") < position(scene_id, "doorbell")
    assert position(scene_id, "payload-ready") < position(scene_id, "fence-write")
    assert position(scene_id, "fence-write") < position(scene_id, "physical-irq")
    assert position(scene_id, "physical-irq") < position(scene_id, "signal-fence")
    assert position(scene_id, "signal-fence") < position(scene_id, "return")
    assert snapshot(scene_id, "submit-return")["fenceMemory"] == "41"
    assert snapshot(scene_id, "payload-ready")["fenceMemory"] == "41"
    assert snapshot(scene_id, "fence-write")["fenceMemory"] == "42"
    assert "尚未" in snapshot(scene_id, "fence-write")["applicationResult"]
    assert "status = 0" in snapshot(scene_id, "return")["waitState"]


def test_gpu_copy_direction_changes_actual_data_edges_and_destination():
    for scene_id, first, second in [
        ("vfio-upload", "read-system", "write-vram"),
        ("vfio-readback", "read-vram", "write-system"),
    ]:
        by_id = {step["id"]: step for step in SCENES[scene_id]["steps"]}
        assert by_id["transfer"]["links"] == [first]
        assert by_id["payload-ready"]["links"] == [second]
    assert "GTT" in snapshot("vfio-upload", "initial")["srcAddress"]
    assert "VRAM" in snapshot("vfio-readback", "initial")["srcAddress"]


def test_gpu_submission_paths_include_vm_preparation_and_real_host_submission():
    for scene_id in ("vfio-upload", "vfio-readback"):
        scene = SCENES[scene_id]
        path = next(path for path in scene["callPaths"] if path["id"] == "guest-submit")
        assert path["sourceIds"] == ["cs", "cs-vm", "cs-submit"]
        submit = next(step for step in scene["steps"] if step["id"] == "submit")
        assert "cs-vm" in submit["sourceIds"]
    scene = SCENES["mdev-copy"]
    path = next(path for path in scene["callPaths"] if path["id"] == "host-schedule")
    assert path["sourceIds"] == [
        "gvt-dispatch", "gvt-scan", "gvt-shadow-context", "gvt-prepare", "host-request-add",
    ]
    assert "调用点" in path["boundary"]
    dispatch = next(step for step in scene["steps"] if step["id"] == "dispatch")
    assert "host-request-add" in dispatch["sourceIds"]
    call = DEMO["sources"]["host-request-add"]
    assert call["symbol"] == "i915_request_add()"
    assert call["path"] == "drivers/gpu/drm/i915/gvt/scheduler.c"
    assert call["line"] == 838


def test_gpu_hardware_evidence_is_not_presented_as_an_executed_c_function():
    for scene_id in ("vfio-upload", "vfio-readback"):
        scene = SCENES[scene_id]
        hardware = next(path for path in scene["callPaths"] if path["id"] == "hardware")
        assert all(DEMO["sources"][source]["kind"] == "hardware"
                   for source in hardware["sourceIds"])
        dispatch = next(path for path in scene["callPaths"] if path["id"] == "guest-dispatch")
        assert "emit-fence" in dispatch["sourceIds"]
        writeback = next(step for step in scene["steps"] if step["id"] == "fence-write")
        assert writeback["sourceIds"] == ["fence-packet"]


def test_gpu_overview_does_not_keep_stale_waiting_or_transferring_labels():
    for scene_id in ("vfio-upload", "vfio-readback"):
        assert "等待调度" not in node_snapshot(scene_id, "transfer")["driver"]
        assert "已被 GPU 获取" in node_snapshot(scene_id, "fetch")["ring"]
        assert "完成" in node_snapshot(scene_id, "fence-write")["gpu"]
    mdev_running = node_snapshot("mdev-copy", "read-source")
    assert "等待调度" not in mdev_running["gvt"]
    assert "交接已完成" in mdev_running["qemu"]
    assert "物理执行已完成" in node_snapshot("mdev-copy", "host-complete")["gpu"]


@pytest.mark.parametrize("scene_id", ["vfio-upload", "vfio-readback"])
def test_direct_doorbell_and_iommu_paths_are_distinct_and_not_per_command_qemu(scene_id):
    text = " ".join(SCENES[scene_id]["assumptions"])
    assert "Vega10" in text and "SDMA 4.0" in text
    assert "BAR" in text and "mmap" in text and "子区间" in text
    state = snapshot(scene_id, "doorbell")
    assert "EPT" in state["cpuAddress"] and "IOMMU" in state["gpuAddress"]
    assert "不经 QEMU" in state["hostHandling"]
    assert "qemu" in {node["id"] for node in SCENES[scene_id]["nodes"]}
    assert all("qemu" not in step["active"] for step in SCENES[scene_id]["steps"])
    assert not any(field["key"] in {"availIdx", "usedIdx"}
                   for section in SCENES[scene_id]["structures"]
                   for field in section["fields"])


def test_mdev_is_a_specific_historical_implementation_not_vfio_with_renamed_labels():
    scene = SCENES["mdev-copy"]
    assumptions = " ".join(scene["assumptions"])
    assert "Skylake" in assumptions and "历史" in assumptions
    assert "独立 VRAM" in assumptions and "系统内存" in assumptions
    assert {"qemu", "gvt", "shadow"} <= {node["id"] for node in scene["nodes"]}
    assert position("mdev-copy", "elsp-write") < position("mdev-copy", "audit")
    assert position("mdev-copy", "audit") < position("mdev-copy", "dispatch")
    assert position("mdev-copy", "write-target") < position("mdev-copy", "host-complete")
    assert position("mdev-copy", "host-complete") < position("mdev-copy", "publish-guest")
    assert position("mdev-copy", "publish-guest") < position("mdev-copy", "notify")
    assert "尚未完成" in snapshot("mdev-copy", "write-target")["guestCompletion"]
    assert "不能" in next(step["detail"] for step in scene["steps"] if step["id"] == "notify")
    assert "独立 VRAM" in snapshot("mdev-copy", "initial")["guestPages"]


def test_gpu_page_has_source_backed_demo_and_explicit_protocol_boundaries():
    page = render_mechanisms_html(Config(), "gpu-execution")
    assert page and "GPU 命令执行" in page
    assert 'id="execution-demo-data"' in page
    assert "AMDGPU" in page and "GVT-g" in page
    assert "SR-IOV" in page and "virtio-gpu" in page
    assert "提交、数据与完成的边界" in page
    assert 'href="interrupt-delivery.html"' in page
    assert 'href="qemu-vfio-dma.html"' in page
    assert "GPU 硬件执行则标注对应命令包" in page and "显示器" in page


def test_gpu_loading_does_not_mutate_checked_in_scene_defaults():
    before = copy.deepcopy(DEMO)
    get_demo("gpu-execution")
    assert DEMO == before


@pytest.mark.parametrize("scene_id", SCENES)
def test_gpu_execution_domains_distinguish_kvm_from_host_device_drivers(scene_id):
    scene = SCENES[scene_id]
    domains = scene["layout"]["domains"]
    assert [domain["component"] for domain in domains] == [
        "guest", "kvm", "qemu", "host-kernel", "hardware",
    ]
    assert [domain["title"] for domain in domains] == [
        "Guest", "KVM", "QEMU", "Host kernel", "物理 GPU",
    ]
    assert domains[1]["nodes"] == ["kvm"]
    assert "kvm" not in domains[3]["nodes"]
    nodes = {node["id"]: node for node in scene["nodes"]}
    assert nodes["app"]["badge"] == "用户态"
    assert nodes["driver"]["badge"] == "内核态"
    assert all(node["label"] != "VFIO / KVM" for node in scene["nodes"])
    represented = [node for domain in domains for node in domain["nodes"]]
    represented += scene["layout"]["memory"]["nodes"]
    assert sorted(represented) == sorted(nodes)


@pytest.mark.parametrize("scene_id", ["vfio-upload", "vfio-readback"])
def test_vfio_mapped_doorbell_reaches_hardware_without_host_job_forwarding(scene_id):
    scene = SCENES[scene_id]
    links = {link["id"]: link for link in scene["links"]}
    step = next(step for step in scene["steps"] if step["id"] == "doorbell")
    assert links["doorbell"]["from"] == "driver"
    assert links["doorbell"]["to"] == "doorbell-bar"
    assert links["doorbell-notify"]["from"] == "doorbell-bar"
    assert links["doorbell-notify"]["to"] == "gpu"
    assert not {"kvm", "qemu", "vfio"} & set(step["active"])
    assert "不逐请求进入 VFIO" in step["detail"]
    assert "不携带 4 KiB" in step["detail"]
    state = snapshot(scene_id, "initial")
    assert "vfio-pci" in state["hostDriver"]
    assert "不由宿主 amdgpu" in state["hostDriver"]
    assert "Guest RAM / GTT" in state["commandMemory"]


@pytest.mark.parametrize("scene_id", ["vfio-upload", "vfio-readback"])
def test_vfio_irq_host_event_routing_and_actual_injection_are_distinct(scene_id):
    assert position(scene_id, "physical-irq") < position(scene_id, "route-irq")
    assert position(scene_id, "route-irq") < position(scene_id, "guest-irq")
    assert "等待 vCPU 注入" in snapshot(scene_id, "route-irq")["irqState"]
    assert "已成功 VM-entry" in snapshot(scene_id, "guest-irq")["irqState"]
    links = {link["id"]: link for link in SCENES[scene_id]["links"]}
    assert (links["irqfd-route"]["from"], links["irqfd-route"]["to"]) == ("vfio", "kvm")
    assert (links["guest-irq"]["from"], links["guest-irq"]["to"]) == ("kvm", "driver")
    paths = {path["id"]: path for path in SCENES[scene_id]["callPaths"]}
    assert paths["completion"]["sourceIds"] == ["vfio-irq"]
    assert "irqfd" in paths["kvm-routing"]["sourceIds"]
    assert "vmx-inject" in paths["kvm-injection"]["sourceIds"]
    assert "irq-handler" in paths["guest-complete"]["sourceIds"]


def test_mdev_exposes_kvm_on_both_trapped_mmio_and_interrupt_paths():
    scene = SCENES["mdev-copy"]
    links = {link["id"]: link for link in scene["links"]}
    for link_id, start, end in [
        ("elsp", "driver", "kvm"),
        ("mmio-userspace", "kvm", "qemu"),
        ("vfio-write", "qemu", "gvt"),
        ("virtual-irq", "gvt", "kvm"),
        ("inject-guest", "kvm", "driver"),
    ]:
        assert (links[link_id]["from"], links[link_id]["to"]) == (start, end)
    assert position("mdev-copy", "elsp-write") < position("mdev-copy", "userspace-mmio")
    assert position("mdev-copy", "userspace-mmio") < position("mdev-copy", "mdev-submit")
    assert position("mdev-copy", "notify") < position("mdev-copy", "inject-irq")
    assert position("mdev-copy", "inject-irq") < position("mdev-copy", "guest-complete")
    assert "尚未进入 Guest" in snapshot("mdev-copy", "notify")["irqState"]
    assert "已成功 VM-entry" in snapshot("mdev-copy", "inject-irq")["irqState"]
    assert "宿主 i915/GVT-g 管理物理 GPU" in snapshot("mdev-copy", "initial")["hostDriver"]


def test_gpu_article_explains_driver_ownership_and_nonserial_domains():
    page = render_mechanisms_html(Config(), "gpu-execution")
    assert "宿主这块 PCI 设备绑定" in page and "vfio-pci" in page
    assert "Guest 内的 AMDGPU 驱动接收应用 CS ioctl" in page
    assert "提交、数据访问和完成通知各有自己的路径" in page
    assert "GVT-g 由宿主 i915/GVT-g 管理物理 GPU" in page
    assert 'href="https://docs.kernel.org/driver-api/vfio.html"' in page
