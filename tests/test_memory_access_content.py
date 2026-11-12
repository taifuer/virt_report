"""Semantic regression checks for the offline memory-access walkthrough."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from virt_report.execution_demos import get_demo, validate_demo
from virt_report.config import Config
from virt_report.render.render import render_mechanisms_html


CONTENT = Path(__file__).resolve().parents[1] / "virt_report" / "content" / "flows"
SCENARIOS = {
    "ram-hit", "tlb-miss", "guest-page-fault", "ept-missing",
    "host-page-fault", "mmio-read", "dirty-write-protect",
    "mmio-kernel-lapic", "mmio-ioeventfd", "mmio-vfio-direct",
    "mmio-ivshmem-direct",
}


@pytest.fixture(scope="module")
def demo() -> dict:
    return json.loads((CONTENT / "memory-access.json").read_text(encoding="utf-8"))


def scene(demo: dict, scenario_id: str) -> dict:
    return next(item for item in demo["scenarios"] if item["id"] == scenario_id)


def snapshots(scenario: dict) -> list[dict[str, str]]:
    state = {
        field["key"]: field["initial"]
        for structure in scenario["structures"]
        for field in structure["fields"]
    }
    result = []
    for step in scenario["steps"]:
        state.update(step["changes"])
        result.append(state.copy())
    return result


def test_memory_demo_schema_sources_and_initial_state(demo):
    validate_demo(demo)
    enriched = get_demo("memory-access")
    assert {**{key: value for key, value in enriched.items() if key != "selector"}, "scenarios": [
        {key: value for key, value in scene.items() if key not in {"layout", "phases"}}
        for scene in enriched["scenarios"]
    ]} == demo
    assert demo["defaultScenario"] == "ram-hit"
    assert {item["id"] for item in demo["scenarios"]} == SCENARIOS
    for item in demo["scenarios"]:
        assert 5 <= len(item["steps"]) <= 10
        assert len(item["nodes"]) <= 6
        first = item["steps"][0]
        assert first["active"] == []
        assert first["changes"] == {}
        assert first["links"] == []
        assert first["nodeStates"] == {}
        assert first["focus"] is True
    for source in demo["sources"].values():
        assert "/blob/" in source["url"] and "#L" in source["url"]


@pytest.mark.parametrize("scenario_id", sorted(item for item in SCENARIOS if not item.startswith("mmio-")))
def test_ram_paths_never_claim_to_return_to_qemu(demo, scenario_id):
    states = snapshots(scene(demo, scenario_id))
    assert all(state["userspace"] == "否" for state in states)
    assert all(state["gva"] == "0x0000000040203080" for state in states)


def test_hardware_walk_does_not_manufacture_a_software_fault(demo):
    item = scene(demo, "tlb-miss")
    assert all("kvm" not in step["active"] for step in item["steps"])
    states = snapshots(item)
    assert states[0]["cache"] == "目标条目缺失"
    assert states[-1]["gpa"] == "0x00403080"
    assert states[-1]["hpa"] == "0x21000080"
    assert states[-1]["value"] == "0x12345678"
    assert all("hva" not in state for state in states)


def test_guest_fault_updates_guest_pte_and_preserves_ept(demo):
    item = scene(demo, "guest-page-fault")
    states = snapshots(item)
    assert states[0]["guest-pte"].startswith("P=0")
    assert states[-1]["guest-pte"].startswith("P=1 / RW=1 / U=1")
    assert states[-1]["cr2"] == "0x0000000040203080"
    assert states[-1]["pf-error"].startswith("0x6")
    assert len({state["ept-leaf"] for state in states}) == 1
    assert all("kvm" not in step["active"] for step in item["steps"])
    assert states[-2]["value"] == "0x00000000（新页已清零）"
    assert states[-1]["value"] == "0x12345678"


def test_ept_missing_resolves_hva_to_pfn_before_installing_leaf(demo):
    item = scene(demo, "ept-missing")
    states = snapshots(item)
    positions = {step["id"]: index for index, step in enumerate(item["steps"])}
    assert positions["slot"] < positions["pfn"] < positions["map"]
    assert states[positions["slot"]]["hva"] == "0x7f0000003000"
    assert states[positions["pfn"]]["pfn"].startswith("0x21000")
    assert states[positions["pfn"]]["ept-leaf"] == "不存在"
    assert states[-1]["hpa"] == "0x21000080"
    assert states[-1]["value"] == "0x12345678"
    assert all(link["kind"] != "translation" for link in item["links"]
               if link["to"] == "host-mm")


def test_host_backing_wait_finishes_before_ept_restore(demo):
    item = scene(demo, "host-page-fault")
    states = snapshots(item)
    positions = {step["id"]: index for index, step in enumerate(item["steps"])}
    assert positions["nowait"] < positions["wait"] < positions["resident"] < positions["map"]
    assert "等待 I/O" in states[positions["wait"]]["thread"]
    assert states[positions["wait"]]["pfn"] == "页面未驻留"
    assert states[positions["resident"]]["pfn"] == "0x21000"
    assert states[positions["resident"]]["ept-leaf"] == "已撤销，等待后备页"
    assert states[-1]["instruction"].startswith("读取完成")
    assert any("异步缺页" in assumption for assumption in item["assumptions"])


def test_mmio_read_is_not_complete_until_reentering_kvm(demo):
    item = scene(demo, "mmio-read")
    states = snapshots(item)
    positions = {step["id"]: index for index, step in enumerate(item["steps"])}
    assert states[positions["userspace"]]["exit-reason"] == "KVM_EXIT_MMIO"
    assert "is_write=0" in states[positions["userspace"]]["mmio-address"]
    assert states[positions["fill"]]["mmio-data"] == "78 56 34 12"
    assert states[positions["fill"]]["value"] == "尚未更新"
    assert "等待再次 KVM_RUN" in states[positions["fill"]]["instruction"]
    assert states[-1]["value"] == "0x12345678"
    assert states[-1]["userspace"] == "已再次调用 KVM_RUN"
    assert all("hpa" not in state for state in states)


def test_dirty_tracking_marks_before_guest_retries_store(demo):
    item = scene(demo, "dirty-write-protect")
    states = snapshots(item)
    positions = {step["id"]: index for index, step in enumerate(item["steps"])}
    assert positions["repair"] < positions["mark"] < positions["retry"]
    assert "W=0" in states[0]["ept-leaf"]
    assert "W=1" in states[positions["repair"]]["ept-leaf"]
    assert states[positions["mark"]]["dirty"].startswith("1")
    assert states[positions["mark"]]["value"] == "0x00000000"
    assert states[-1]["value"] == "0x12345678"
    assert len({state["guest-pte"] for state in states}) == 1
    assert any("未启用 Dirty Ring 或硬件 PML" in assumption
               for assumption in item["assumptions"])


@pytest.mark.parametrize("scenario_id", [
    "mmio-kernel-lapic", "mmio-ioeventfd", "mmio-vfio-direct", "mmio-ivshmem-direct",
])
def test_additional_mmio_paths_do_not_return_kvm_run_to_qemu(demo, scenario_id):
    item = scene(demo, scenario_id)
    states = snapshots(item)
    assert all(state["userspace"] == "否" for state in states)
    assert all("KVM_EXIT_MMIO" not in state.get("hw-exit", "") for state in states)
    assert states[-1]["instruction"].endswith("完成")


def test_kernel_lapic_read_exits_but_is_completed_by_kernel(demo):
    item = scene(demo, "mmio-kernel-lapic")
    states = snapshots(item)
    assert states[1]["hw-exit"].startswith("已发生")
    assert states[-1]["value"] == "0x01000000"
    assert any("关闭" in text and "硬件加速" in text for text in item["assumptions"])
    assert "lapic-mmio-read" in {id for step in item["steps"] for id in step["sourceIds"]}


def test_ioeventfd_only_completes_matching_write_not_backend_work(demo):
    item = scene(demo, "mmio-ioeventfd")
    states = snapshots(item)
    assert states[1]["hw-exit"].startswith("已发生")
    assert states[-1]["event"] == "已触发"
    assert states[-1]["backend"] == "已通知；IOThread 独立处理队列"
    assert any("datamatch" in text for text in item["assumptions"])


def test_vfio_bar_steady_mapping_is_not_device_dma_or_qemu_callback(demo):
    item = scene(demo, "mmio-vfio-direct")
    states = snapshots(item)
    assert {state["hw-exit"] for state in states} == {"无"}
    assert all("qemu" not in step["active"] and "kvm" not in step["active"] for step in item["steps"])
    assert any("MSI-X" in text and "首次" in text for text in item["assumptions"])
    assert any("不是设备 DMA" in text for text in item["assumptions"])


@pytest.mark.parametrize("scenario_id,hpa,target", [
    ("mmio-vfio-direct", "0xd0001000", "物理设备 MMIO"),
    ("mmio-ivshmem-direct", "0x21001000", "宿主共享 RAM"),
])
def test_bar_comparison_resolves_the_same_gpa_to_different_host_targets(demo, scenario_id, hpa, target):
    item = scene(demo, scenario_id)
    states = snapshots(item)
    assert states[0]["hpa"] == "待本次转换"
    assert states[1]["hpa"] == hpa
    assert [step["id"] for step in item["steps"]] == [
        "ready", "translate", "access", "return", "complete",
    ]
    for state in states:
        assert int(state["guest-bar-base"], 16) + int(state["offset"], 16) == int(state["gpa"], 16)
        assert state["gpa"] == "0xf0001000"
        assert state["offset"] == "0x1000"
        assert state["target"] == target
        assert state["hw-exit"] == "无" and state["userspace"] == "否"
    assert states[-2]["value"] == "旧值"
    assert states[-1]["value"] == "0x12345678"
    assert [field["key"] for field in item["completion"]] == ["gpa", "hpa", "offset", "target"]
    assert all(not {"qemu", "kvm"} & set(step["active"]) for step in item["steps"])
    assert all(part["kind"] == "instruction" for part in item["sourceSteps"])


def test_vfio_host_address_is_a_device_window_not_ram_or_a_dma_iova(demo):
    item = scene(demo, "mmio-vfio-direct")
    state = snapshots(item)[-1]
    assert int(state["host-bar-base"], 16) + int(state["offset"], 16) == int(state["hpa"], 16)
    assert state["pci-address"] == "0xd0001000（本例恒等窗口）"
    assert {node["id"] for node in item["nodes"]} == {"guest", "mmu", "device"}
    assert any("恒等" in text and "宿主桥" in text for text in item["assumptions"])
    assert item["callPaths"][0]["sourceIds"] == ["vfio-region-map", "ram-device-map"]


def test_ivshmem_bar2_reads_existing_shared_ram_without_vfio_or_device_callbacks(demo):
    item = scene(demo, "mmio-ivshmem-direct")
    assert {node["id"] for node in item["nodes"]} == {"guest", "mmu", "ram"}
    assert {state["memory-value"] for state in snapshots(item)} == {"0x12345678"}
    assert {state["backing"] for state in snapshots(item)} == {"share=on 的宿主共享 RAM"}
    assert any("BAR0" in text and "BAR2" in text for text in item["assumptions"])
    assert any("不保证整个 BAR2" in text and "连续" in text for text in item["assumptions"])
    assert "vfio-region-map" not in {source for path in item["callPaths"] for source in path["sourceIds"]}
    assert (demo["sources"]["ivshmem-setup"]["path"], demo["sources"]["ivshmem-setup"]["line"]) == (
        "hw/misc/ivshmem-pci.c", 845,
    )
    assert demo["sources"]["ivshmem-spec"]["path"] == "docs/specs/ivshmem-spec.rst"


def test_memory_article_explains_resource_dispatch_without_retired_postcopy_article():
    page = render_mechanisms_html(Config(), "memory-virtualization")
    routing = page.split('<section id="address-routing"', 1)[1].split('</section>', 1)[0]
    assert all(text in routing for text in (
        "PCI 枚举", "readl()", "MemoryRegion", "memslot", "KVM_EXIT_MMIO",
        "普通 Guest RAM", "VFIO 物理 BAR0", "ivshmem 共享 BAR2", "CPU 物理地址与 PCI 总线地址",
    ))
    assert 'href="qemu-postcopy.html"' not in page
    assert not any("QEMU 建 EPT" == item["label"] for item in get_demo("memory-access")["scenarios"])


def test_memory_source_paths_name_symbols_and_keep_context_boundaries(demo):
    for item in demo["scenarios"]:
        assert item["callPaths"]
        for path in item["callPaths"]:
            assert path["context"] and path["boundary"]
            for source_id in path["sourceIds"]:
                source = demo["sources"][source_id]
                assert source["symbol"] and source["path"] and source["line"] > 0
                assert len(source["revision"]) == 40


def test_qemu_mmio_has_real_request_device_and_completion_paths(demo):
    item = next(scene for scene in demo["scenarios"] if scene["id"] == "mmio-read")
    steps = {step["id"]: step for step in item["steps"]}
    assert "mmio-request" in steps["userspace"]["sourceIds"]
    assert steps["device"]["sourceIds"] == [
        "qemu-address-read", "qemu-memory-read", "qemu-device-read",
    ]
    assert steps["complete"]["sourceIds"] == [
        "mmio-complete-fn", "mmio-complete-read", "mmio-resume-instruction",
    ]
    source = demo["sources"]["mmio-complete-read"]
    assert (source["symbol"], source["path"], source["line"]) == (
        "complete_emulated_mmio()", "arch/x86/kvm/x86.c", 11761,
    )
    assert demo["sources"]["mmio-completion"]["kind"] == "spec"
    assert demo["sources"]["mmio-qemu"]["symbol"] == "kvm_cpu_exec()"


@pytest.mark.parametrize("scene_id", ["ram-hit", "tlb-miss", "mmio-vfio-direct", "mmio-ivshmem-direct"])
def test_hardware_memory_paths_explain_instruction_steps_without_inventing_calls(demo, scene_id):
    item = next(scene for scene in demo["scenarios"] if scene["id"] == scene_id)
    assert item["sourceSteps"]
    assert all(part["kind"] == "instruction" for part in item["sourceSteps"])
    assert {step_id for part in item["sourceSteps"] for step_id in part["stepIds"]} == {
        step["id"] for step in item["steps"]
    }
    assert {source["kind"] for source in demo["sources"].values()} <= {
        "function", "assembly", "spec", "reference",
    }
