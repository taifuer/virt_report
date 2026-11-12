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
DPDK_COMMIT = "1b3bf1128d9bda5595861814792f74b8f57160c8"


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
        elif name == "interrupt-delivery":
            assert 7 <= len(scene["steps"]) <= 20
            assert len(scene["nodes"]) <= 8
        else:
            assert 5 <= len(scene["steps"]) <= 10
            assert len(scene["nodes"]) <= 6


@pytest.mark.parametrize("name", ["cpu-execution", "interrupt-delivery"])
def test_execution_sources_are_fixed_code_or_official_architecture_manual(name):
    for source in load_demo(name)["sources"].values():
        url = urlsplit(source["url"])
        if url.netloc == "github.com":
            repository = "/".join(url.path.split("/")[1:3])
            expected = {"torvalds/linux": LINUX_COMMIT, "qemu/qemu": QEMU_COMMIT,
                        "DPDK/dpdk": DPDK_COMMIT}[repository]
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
        "mmio-userspace", "hlt-wakeup", "hlt-passthrough",
        "sleep-other-task", "sleep-idle-vcpu",
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
        "notify-suppressed", "vtd-remapped", "vtd-posted",
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
    assert trace["ready"]["used"] == "7"
    assert trace["eoi"]["used"] == trace["used-publish"]["used"] == "8"


def test_interrupt_article_diagram_preserves_its_rx_completion_byte_count():
    article = (TEMPLATES / "_interrupt_overview_article.html").read_text()
    diagram = (CONTENT / "diagrams/interrupt-completion.mmd").read_text()
    exported = (CONTENT.parent / "render/assets/interrupt-completion.svg").read_text()
    assert "1500 字节帧与 12 字节网络头，共 1512 字节" in article
    assert "used.ring[7] = id 3, len 1512" in diagram
    assert "used.ring[7] = id 3, len 1512" in exported
    assert "len 512" not in diagram


@pytest.mark.parametrize("scene_id", ["msix-software", "irqfd", "if-masked", "msix-masked"])
def test_software_interrupt_preparation_is_not_guest_handler_entry(scene_id):
    trace = states(scenario("interrupt-delivery", scene_id))
    assert trace["inject"]["delivery"] == "注入字段已准备，尚未进入 Guest"
    assert trace["guest-entry"]["delivery"] == "Guest handler 已进入"
    assert trace["inject"]["irr"] == "0"
    assert trace["inject"]["isr"] == "1"
    assert trace["guest-entry"]["last-used"] == "7"


@pytest.mark.parametrize("scene_id", ["msix-software", "irqfd", "if-masked", "msix-masked", "apicv-posted", "notify-suppressed"])
def test_interrupt_starts_at_device_data_before_used_publication(scene_id):
    item = scenario("interrupt-delivery", scene_id)
    trace = states(item)
    ids = [step["id"] for step in item["steps"]]
    assert ids[:5] == ["ready", "write-data", "used-fill", "used-publish", "notify-check"]
    assert trace["ready"]["payload"] == "尚未写入"
    assert trace["write-data"]["used"] == "7"
    assert trace["used-fill"]["used-entry"] == "id=0 · len=72"
    assert trace["used-fill"]["used"] == "7"
    assert trace["used-publish"]["used"] == "8"
    assert trace["used-publish"]["last-used"] == "7"


def test_suppressed_notification_has_no_interrupt_but_explicit_poll():
    item = scenario("interrupt-delivery", "notify-suppressed")
    trace = states(item)
    assert "poll" in trace
    assert trace["notify-check"]["notify-decision"] == "抑制通知；完成保留"
    assert all(snapshot["irr"] == snapshot["isr"] == "0" for snapshot in trace.values())
    assert trace["consume"]["last-used"] == "8"
    assert all(step["id"] not in {"notify", "inject", "guest-entry"} for step in item["steps"])


@pytest.mark.parametrize("scene_id", ["ordinary-add", "guest-syscall", "cpuid-kernel", "mmio-userspace"])
def test_cpu_basics_share_running_vcpu_start_and_explicit_execution_context(scene_id):
    item = scenario("cpu-execution", scene_id)
    trace = states(item)
    assert item["presentation"] == "cpu-timeline"
    assert trace["ready"]["mode"] == "non-root"
    assert "调用中" in trace["ready"]["run-state"]
    assert {"rip", "cpl", "mode", "run-state", "hw-exit"} <= set(item["timeline"]["fields"])
    assert item["links"] == []  # No fictional physical CPU -> Guest request transfer.
    lanes = {lane["id"] for lane in item["timeline"]["lanes"]}
    assert all(step["execution"]["lane"] in lanes for step in item["steps"])
    assert all(step["execution"]["instruction"] for step in item["steps"])


def test_add_execution_does_not_repeat_kvm_entry_or_invent_kvm_instruction_handler():
    item = scenario("cpu-execution", "ordinary-add")
    trace = states(item)
    assert {value["mode"] for value in trace.values()} == {"non-root"}
    assert {value["run-state"] for value in trace.values()} == {"调用中，未返回"}
    assert all(step["execution"]["lane"] == "guest-user" for step in item["steps"])
    assert all(step["sourceIds"] == ["sdm"] for step in item["steps"][1:])


def test_syscall_sources_follow_linux_618_guest_entry_dispatch_and_sysret():
    demo = load_demo("cpu-execution")
    item = scenario("cpu-execution", "guest-syscall")
    sources = demo["sources"]
    assert item["callPaths"][0]["sourceIds"] == [
        "guest-syscall-entry", "guest-syscall-dispatch", "guest-syscall-x64",
        "guest-syscall-switch", "guest-getpid",
    ]
    expected = {
        "guest-syscall-entry": ("arch/x86/entry/entry_64.S", 87, "assembly"),
        "guest-syscall-dispatch": ("arch/x86/entry/syscall_64.c", 87, "function"),
        "guest-syscall-x64": ("arch/x86/entry/syscall_64.c", 53, "function"),
        "guest-syscall-switch": ("arch/x86/entry/syscall_64.c", 35, "function"),
        "guest-getpid": ("kernel/sys.c", 999, "function"),
        "guest-sysret": ("arch/x86/entry/entry_64.S", 137, "assembly"),
    }
    for source_id, (path, line, kind) in expected.items():
        source = sources[source_id]
        assert (source["path"], source["line"], source["kind"]) == (path, line, kind)
        assert f"#L{line}" in source["url"]
    steps = {step["id"]: step for step in item["steps"]}
    assert "guest-syscall-entry" in steps["kernel"]["sourceIds"]
    assert "guest-getpid" in steps["result"]["sourceIds"]
    assert "guest-sysret" in steps["return"]["sourceIds"]
    assert "switch" in steps["result"]["detail"]
    assert "SYSCALL_DEFINE0(getpid)" in sources["guest-getpid"]["label"]
    assert [part["stepIds"] for part in item["sourceSteps"]] == [
        ["ready", "save", "kernel"], ["return"],
    ]


def test_add_source_view_has_instruction_semantics_not_a_fictitious_kvm_handler():
    item = scenario("cpu-execution", "ordinary-add")
    assert all(part["kind"] == "instruction" for part in item["sourceSteps"])
    assert all(part["sourceIds"] == ["sdm"] for part in item["sourceSteps"])
    assert "ADD EAX, 1" in item["sourceSteps"][0]["code"]
    assert {step_id for part in item["sourceSteps"] for step_id in part["stepIds"]} == {
        step["id"] for step in item["steps"]
    }
    assert "准备条件" in item["callPaths"][1]["context"]


@pytest.mark.parametrize("name", ["cpu-execution", "interrupt-delivery"])
def test_source_kinds_separate_code_assembly_and_supporting_references(name):
    assert {source["kind"] for source in load_demo(name)["sources"].values()} <= {
        "function", "assembly", "spec", "reference",
    }


def test_irq_software_delivery_and_guest_ack_reference_actual_functions():
    sources = load_demo("interrupt-delivery")["sources"]
    assert sources["deliver"]["symbol"] == "vmx_deliver_interrupt()"
    assert sources["deliver"]["line"] == 4242
    assert sources["posted"]["line"] == 159
    assert sources["irq-ack"]["path"] == "kernel/irq/chip.c"
    assert sources["irq-ack"]["line"] == 823
    assert load_demo("cpu-execution")["sources"]["irq"]["symbol"] == "vmx_deliver_interrupt()"


def test_apicv_hardware_steps_do_not_claim_software_lapic_functions_execute():
    item = scenario("interrupt-delivery", "apicv-posted")
    steps = {step["id"]: step for step in item["steps"]}
    for step_id in ["sync", "accept", "eoi"]:
        assert steps[step_id]["sourceIds"] == ["sdm"]
    assert "eoi" not in {source_id for path in item["callPaths"]
                         for source_id in path["sourceIds"]}
    assert "posted-notify" in steps["notify"]["sourceIds"]
    assert {part["stepIds"][0] for part in item["sourceSteps"]} == {"sync", "accept", "eoi"}


def test_suppressed_irq_uses_napi_poll_instead_of_a_fictitious_irq_callback():
    item = scenario("interrupt-delivery", "notify-suppressed")
    path = item["callPaths"][-1]
    assert path["sourceIds"] == ["guest-napi-poll", "guest-receive", "consume"]
    assert "不经过 IRQ" in path["context"]


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


@pytest.mark.parametrize("stem,sections", [
    ("cpu_overview", ["preface", "paths", "walkthrough", "comparison",
                      "evolution", "evaluation", "sources"]),
    ("interrupt_overview", ["preface", "example", "controllers", "notification",
                            "delivery", "irqfd", "acceleration", "walkthrough",
                            "implementation", "evolution",
                            "evaluation", "sources"]),
])
def test_articles_have_main_line_shared_player_and_existing_local_links(stem, sections):
    article = (TEMPLATES / f"_{stem}_article.html").read_text()
    assert "{% include '_execution_lab.html' %}" in article
    assert re.findall(r'<section id="([^"]+)"', article) == sections
    known = {item["slug"] for item in
             json.loads((CONTENT / "mechanisms.json").read_text())["articles"]}
    for target in re.findall(r'href="([a-z0-9-]+)\.html(?:#[^"]*)?"', article):
        assert target in known, target
