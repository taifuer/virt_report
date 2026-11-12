"""HLT passthrough is hardware waiting, not a blocked KVM task or a poll loop."""
import pytest

from virt_report.config import Config
from virt_report.execution_demos import get_demo
from virt_report.render.render import render_mechanisms_html


def scene(scene_id="hlt-passthrough"):
    return next(item for item in get_demo("cpu-execution")["scenarios"]
                if item["id"] == scene_id)


def snapshots(item):
    values = {field["key"]: field["initial"]
              for group in item["structures"] for field in group["fields"]}
    result = {}
    for step in item["steps"]:
        values.update(step["changes"])
        result[step["id"]] = dict(values)
    return result


def test_idle_presets_are_adjacent_without_changing_default_or_old_urls():
    demo = get_demo("cpu-execution")
    assert demo["defaultScenario"] == "ordinary-add"
    waiting = next(group for group in demo["selector"]["groups"]
                   if group["label"] == "等待与唤醒")
    assert waiting["scenarios"][:2] == [
        {"id": "hlt-wakeup", "label": "HLT · KVM 阻塞等待"},
        {"id": "hlt-passthrough", "label": "HLT · Idle Passthrough"},
    ]
    assert [phase["start"] for phase in scene()["phases"]] == [
        "ready", "post", "deliver", "eoi",
    ]
    assert scene()["completion"] == scene("hlt-wakeup")["completion"]
    assert [field["key"] for field in scene()["completion"]] == [
        "mode", "exit-count", "task-state", "run-state",
    ]


def test_hardware_halt_does_not_change_kvm_mp_state_or_block_host_task():
    trace = snapshots(scene())
    assert list(trace) == ["ready", "halt", "post", "deliver", "eoi", "resume"]
    for values in trace.values():
        assert values["hlt-exiting"] == "0（不截获）"
        assert values["exit-count"] == "0"
        assert values["mode"] == "non-root"
        assert values["mp-state"] == "RUNNABLE"
        assert values["task-state"] == "TASK_RUNNING，未阻塞"
        assert values["wait"] == "未发布"
        assert values["run-state"] == "调用中，未返回"
    assert trace["ready"]["activity"] == "active"
    assert trace["halt"]["activity"] == trace["post"]["activity"] == "HLT（硬件等待）"
    assert trace["halt"]["rip"] == "0xffffffff81001001（HLT 之后）"
    assert trace["deliver"]["activity"] == trace["resume"]["activity"] == "active"
    assert trace["resume"]["rip"] == trace["halt"]["rip"]


def test_intercepted_halt_remains_a_distinct_exit_then_block_then_schedule_path():
    trace = snapshots(scene("hlt-wakeup"))
    assert trace["ready"]["exit-count"] == "0"
    assert trace["halt"]["exit-count"] == trace["resume"]["exit-count"] == "1（HLT）"
    assert trace["halt"]["mode"] == "已退出 Guest"
    assert trace["halt"]["mp-state"] == trace["sleep"]["mp-state"] == "HALTED"
    assert "已睡眠" in trace["sleep"]["task-state"]
    assert "等待调度" in trace["wake"]["task-state"]
    assert trace["scheduled"]["mp-state"] == "RUNNABLE"
    assert trace["resume"]["mode"] == "non-root"
    assert all(value["run-state"] == "调用中，未返回" for value in trace.values())


def test_posted_event_has_one_notification_and_separate_guest_acknowledgement():
    trace = snapshots(scene())
    assert trace["halt"]["pir"] == trace["halt"]["on"] == "0"
    assert trace["post"]["pir"] == trace["post"]["on"] == "1"
    assert trace["post"]["isr"] == "0"
    assert trace["deliver"]["pir"] == trace["deliver"]["on"] == "0"
    assert trace["deliver"]["isr"] == "1"
    assert trace["deliver"]["if"] == "0（Guest interrupt gate）"
    assert trace["eoi"]["isr"] == "0"
    assert trace["resume"]["if"] == "1"
    assert sum("notify" in step["links"] for step in scene()["steps"]) == 1
    assert all(trace[step]["notification"] == "已由目标 CPU 处理"
               for step in ("deliver", "eoi", "resume"))


@pytest.mark.parametrize("scene_id", ["hlt-wakeup", "hlt-passthrough"])
def test_node_descriptions_advance_with_waiting_and_interrupt_state(scene_id):
    item = scene(scene_id)
    nodes = {node["id"]: node["initial"] for node in item["nodes"]}
    trace = {}
    for step in item["steps"]:
        nodes.update(step["nodeStates"])
        trace[step["id"]] = dict(nodes)
    assert "准备执行" not in trace["halt"]["guest"]
    if scene_id == "hlt-wakeup":
        assert "已阻塞" in trace["sleep"]["kvm"]
        assert "已发布" in trace["wake"]["backend"]
        assert "已交付" in trace["resume"]["lapic"]
        assert "继续执行 Guest" in trace["resume"]["scheduler"]
    else:
        assert "已发送" in trace["deliver"]["sender"]
        assert "PIR = 0" in trace["resume"]["pid"]
        assert "继续 idle" in trace["resume"]["guest"]


def test_posting_cpu_and_pid_are_not_confused_with_target_vcpu_or_guest_ram():
    item = scene()
    domains = {domain["component"]: domain for domain in item["layout"]["domains"]}
    assert domains["kvm"]["nodes"] == ["sender"]
    assert "另一宿主 CPU" in domains["kvm"]["context"]
    assert domains["host-kernel"]["nodes"] == ["thread"]
    assert domains["qemu"]["nodes"] == []
    assert domains["hardware"]["nodes"] == ["cpu"]
    assert item["layout"]["memory"]["title"] == "宿主内存 · PID"
    assert item["layout"]["memory"]["nodes"] == ["pid"]
    assert not any(link["from"] == "guest" and link["to"] == "sender"
                   for link in item["links"])
    paths = {path["id"]: path for path in item["callPaths"]}
    assert "非逐次等待路径" in paths["setup"]["context"]
    assert paths["post"]["entry"]["kind"] == "notification"
    assert "另一宿主 CPU" in paths["post"]["entry"]["label"]
    assert paths["post"]["sourceIds"] == ["idle-posted", "idle-pir", "idle-notify"]
    hardware = {step["id"]: step for step in item["sourceSteps"]}
    assert hardware["hlt-hardware"]["sourceIds"] == ["sdm"]
    assert hardware["posted-hardware"]["sourceIds"] == ["sdm"]
    assert all(node["initial"] for node in item["nodes"])


def test_exitless_wakeup_has_explicit_hardware_and_scheduling_preconditions():
    assumptions = " ".join(scene()["assumptions"])
    for term in ("HLT exiting = 0", "创建 vCPU 前", "独占", "不超卖",
                 "posted-interrupt processing", "virtual-interrupt delivery",
                 "另一宿主 CPU", "IF = 1", "interrupt shadow", "EOI-exit bitmap",
                 "不使用 VT-d", "不是忙轮询", "MONITOR/MWAIT", "默认关闭"):
        assert term in assumptions
    assert "不要求 posted" in assumptions
    assert "不是所有唤醒路径的保证" in scene()["summary"]
    detail = scene()["steps"][-1]["detail"]
    assert "普通 IRQ、宿主定时器或抢占仍可能导致退出" in detail


@pytest.mark.parametrize("slug", ["cpu-virtualization", "kvm-vcpu-halt"])
def test_rendered_articles_explain_scope_and_share_the_new_preset(slug, article_source):
    page = render_mechanisms_html(Config(), slug)
    for term in ("Idle Passthrough", "HLT exiting", "cpu-pm=on", "默认关闭",
                 "RUNNABLE", "MONITOR/MWAIT", "独占逻辑 CPU"):
        assert term in page
    assert "kvm-cap-x86-disable-exits" in article_source(slug)
    if slug == "cpu-virtualization":
        assert 'data-execution-scenario' in page
        assert '"id": "hlt-passthrough"' in page
        assert "HLT · KVM 阻塞等待" in page
    else:
        assert "cpu-virtualization.html?scenario=hlt-passthrough#walkthrough" in page
        assert "本文的轮询实验仍固定在截获路径" in page


@pytest.mark.parametrize("slug", ["cpu-virtualization", "kvm-vcpu-run"])
def test_related_overviews_qualify_hlt_interception_and_link_the_comparison(slug):
    page = render_mechanisms_html(Config(), slug)
    if slug == 'cpu-virtualization':
        assert '在 HLT 截获场景中' in page
        assert '"id": "hlt-passthrough"' in page
    else:
        assert "启用 HLT 截获" in page
        assert "cpu-virtualization.html?scenario=hlt-passthrough#walkthrough" in page
    assert "Idle Passthrough" in page
