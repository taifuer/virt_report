"""Offline semantic checks for CPU execution and interrupt teaching traces."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from virt_report.execution_demos import validate_demo


CONTENT = Path(__file__).resolve().parents[1] / "virt_report" / "content"
TEMPLATES = CONTENT.parent / "render" / "templates"
LINUX_COMMIT = "7d0a66e4bb9081d75c82ec4957c50034cb0ea449"
QEMU_COMMIT = "698104725efad4b29079d857dfdebbd804e34c99"


def load_demo(name):
    return json.loads((CONTENT / "flows" / f"{name}.json").read_text())


def scenario(name, scene_id):
    return next(scene for scene in load_demo(name)["scenarios"]
                if scene["id"] == scene_id)


def states(scene):
    """Apply only explicit field patches, as the interactive model does."""
    current = {field["key"]: field["initial"]
               for structure in scene["structures"]
               for field in structure["fields"]}
    result = {}
    for step in scene["steps"]:
        current.update(step["changes"])
        result[step["id"]] = dict(current)
    return result


@pytest.mark.parametrize("name", ["cpu-execution", "interrupt-delivery"])
def test_execution_content_has_valid_references_and_neutral_initial_state(name):
    demo = load_demo(name)
    validate_demo(demo)
    for scene in demo["scenarios"]:
        first = scene["steps"][0]
        assert first["id"] == "ready"
        assert first["active"] == []
        assert first["changes"] == {}
        assert first["links"] == []
        assert first["nodeStates"] == {}
        assert first["focus"] is True
        if scene["id"] in {"sleep-other-task", "sleep-idle-vcpu"}:
            assert 10 < len(scene["steps"]) <= 20
            assert len(scene["nodes"]) == 7
        else:
            assert 5 <= len(scene["steps"]) <= 10
            assert len(scene["nodes"]) <= 6


@pytest.mark.parametrize("name", ["cpu-execution", "interrupt-delivery"])
def test_execution_sources_are_fixed_code_or_official_architecture_manual(name):
    for source in load_demo(name)["sources"].values():
        url = urlsplit(source["url"])
        if url.netloc == "github.com":
            expected = LINUX_COMMIT if "/torvalds/linux/" in url.path else QEMU_COMMIT
            assert f"/blob/{expected}/" in url.path
            assert re.fullmatch(r"L\d+(?:-L\d+)?", url.fragment)
        else:
            assert url.netloc == "www.intel.com"
            assert url.path.endswith("/intel-sdm.html")


def test_cpu_scenarios_cover_distinct_execution_boundaries():
    demo = load_demo("cpu-execution")
    assert demo["defaultScenario"] == "ordinary-add"
    assert {scene["id"] for scene in demo["scenarios"]} == {
        "ordinary-add", "guest-syscall", "cpuid-kernel",
        "mmio-userspace", "hlt-wakeup", "sleep-other-task", "sleep-idle-vcpu",
    }


def test_add_and_guest_syscall_do_not_claim_userspace_returns():
    add = states(scenario("cpu-execution", "ordinary-add"))
    assert add["continue"]["eax"] == "42"
    assert add["continue"]["rip"] == "0x400003"
    assert add["continue"]["hw-exit"] == "无"
    assert add["continue"]["run-state"] == "调用中，未返回"
    syscall = states(scenario("cpu-execution", "guest-syscall"))
    assert syscall["kernel"]["cpl"] == "0"
    assert syscall["return"]["cpl"] == "3"
    assert syscall["return"]["rip"] == syscall["save"]["rcx"]
    assert {state["mode"] for state in syscall.values()} == {"non-root"}
    assert {state["hw-exit"] for state in syscall.values()} == {"无"}


def test_cpuid_vmexit_stays_inside_kvm_run():
    trace = states(scenario("cpu-execution", "cpuid-kernel"))
    assert trace["exit"]["hw-exit"] == "EXIT_REASON_CPUID"
    assert trace["exit"]["rip"] == "0x400200"
    assert trace["advance"]["rip"] == "0x400202"
    assert {state["run-state"] for state in trace.values()} == {"调用中，未返回"}
    assert trace["resume"]["mode"] == "non-root"


def test_mmio_read_completes_only_after_second_kvm_run():
    trace = states(scenario("cpu-execution", "mmio-userspace"))
    assert trace["request"]["exit-reason"] == "KVM_EXIT_MMIO"
    assert trace["request"]["direction"] == "0（读）"
    assert trace["read"]["data"] == "78 56 34 12"
    assert trace["read"]["eax"] == "旧值"
    assert trace["read"]["rip"] == "0x400300"
    assert trace["complete"]["eax"] == "0x12345678"
    assert trace["complete"]["run-state"] == "第二次调用中"
    assert trace["complete"]["rip"] == "0x400302"


def test_halt_separates_halted_sleeping_and_runnable_waiting():
    trace = states(scenario("cpu-execution", "hlt-wakeup"))
    assert trace["halt"]["mp-state"] == "HALTED"
    assert "尚在执行" in trace["prepare"]["task-state"]
    assert "已睡眠" in trace["sleep"]["task-state"]
    assert "等待调度" in trace["wake"]["task-state"]
    assert trace["wake"]["mp-state"] == "HALTED"
    assert trace["scheduled"]["mp-state"] == "RUNNABLE"
    assert trace["scheduled"]["wait"] == "已撤销"
    assert {state["run-state"] for state in trace.values()} == {"调用中，未返回"}


def test_interrupt_scenarios_preserve_independent_masking_layers():
    demo = load_demo("interrupt-delivery")
    assert demo["defaultScenario"] == "msix-software"
    assert {scene["id"] for scene in demo["scenarios"]} == {
        "msix-software", "irqfd", "if-masked", "msix-masked", "apicv-posted",
    }
    trace = states(scenario("interrupt-delivery", "msix-masked"))
    assert trace["pending"]["pba"] == "1"
    assert trace["pending"]["irr"] == "0"
    assert trace["send"]["pba"] == "0"
    assert trace["send"]["irr"] == "0"
    assert trace["lapic"]["irr"] == "1"
    assert trace["inject"]["isr"] == "1"


def test_if_enabling_keeps_interrupt_pending_until_shadow_ends():
    trace = states(scenario("interrupt-delivery", "if-masked"))
    assert trace["pending"]["irr"] == "1"
    assert trace["blocked"]["isr"] == "0"
    assert trace["sti"]["if"] == "1"
    assert trace["sti"]["shadow"] == "1"
    assert trace["sti"]["irr"] == "1"
    assert trace["window"]["shadow"] == "0"
    assert trace["inject"]["irr"] == "0"
    assert trace["inject"]["isr"] == "1"
    assert {state["pba"] for state in trace.values()} == {"0（未在设备侧屏蔽）"}


@pytest.mark.parametrize("scene_id", ["msix-software", "irqfd"])
def test_eoi_does_not_consume_shared_queue(scene_id):
    trace = states(scenario("interrupt-delivery", scene_id))
    assert trace["eoi"]["isr"] == "0"
    assert trace["eoi"]["last-used"] == "7"
    assert trace["consume"]["last-used"] == "8"
    assert trace["eoi"]["used"] == trace["ready"]["used"]


def test_posted_interrupt_distinguishes_pir_virr_and_visr():
    scene = scenario("interrupt-delivery", "apicv-posted")
    assert any("正在" in condition and "non-root" in condition
               for condition in scene["assumptions"])
    assert any("EOI-exit bitmap" in condition for condition in scene["assumptions"])
    trace = states(scene)
    assert trace["post"]["pir"] == "1"
    assert trace["post"]["virr"] == "0"
    assert trace["sync"]["pir"] == "0"
    assert trace["sync"]["on"] == "0"
    assert trace["sync"]["virr"] == "1"
    assert trace["sync"]["visr"] == "0"
    assert trace["accept"]["virr"] == "0"
    assert trace["accept"]["visr"] == "1"
    assert trace["eoi"]["visr"] == "0"
    assert {state["exit"] for state in trace.values()} == {"0"}


@pytest.mark.parametrize("stem", ["cpu_execution", "interrupt_delivery"])
def test_articles_have_main_line_shared_player_and_existing_local_links(stem):
    article = (TEMPLATES / f"_{stem}_article.html").read_text()
    assert "{% include '_execution_lab.html' %}" in article
    assert re.findall(r'<section id="([^"]+)"', article) == [
        "preface", "walkthrough", "comparison", "evolution", "sources",
    ]
    known = {item["slug"] for item in
             json.loads((CONTENT / "mechanisms.json").read_text())["articles"]}
    for target in re.findall(r'href="([a-z0-9-]+)\.html(?:#[^"]*)?"', article):
        assert target in known, target
