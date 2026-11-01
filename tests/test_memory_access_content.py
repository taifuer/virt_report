"""Semantic regression checks for the offline memory-access walkthrough."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from virt_report.execution_demos import get_demo, validate_demo


CONTENT = Path(__file__).resolve().parents[1] / "virt_report" / "content" / "flows"
SCENARIOS = {
    "ram-hit", "tlb-miss", "guest-page-fault", "ept-missing",
    "host-page-fault", "mmio-read", "dirty-write-protect",
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
        {key: value for key, value in scene.items() if key != "layout"}
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


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS - {"mmio-read"}))
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
