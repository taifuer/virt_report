"""Keep Guest task, vCPU and host scheduling timelines distinct."""
import json
from pathlib import Path

import pytest

from virt_report.execution_demos import validate_demo


ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "virt_report" / "content" / "flows"
TIMER_SCENES = ("sleep-other-task", "sleep-idle-vcpu")
BLOCKED = "TASK_INTERRUPTIBLE | TASK_FREEZABLE，已阻塞"
READY = "TASK_RUNNING，就绪但尚未执行"


def load_scene(scene_id):
    demo = json.loads((CONTENT / "cpu-execution.json").read_text())
    return next(scene for scene in demo["scenarios"] if scene["id"] == scene_id)


def snapshots(scene):
    """Apply both field and node patches exactly in their displayed order."""
    fields = {field["key"]: field["initial"]
              for structure in scene["structures"]
              for field in structure["fields"]}
    nodes = {node["id"]: node.get("initial", "准备就绪")
             for node in scene["nodes"]}
    result = {}
    for step in scene["steps"]:
        fields.update(step["changes"])
        nodes.update(step["nodeStates"])
        result[step["id"]] = (dict(fields), dict(nodes))
    return result


@pytest.mark.parametrize("scene_id", TIMER_SCENES)
def test_timer_scenarios_are_integrated_with_neutral_explicit_initial_state(scene_id):
    demo = json.loads((CONTENT / "cpu-execution.json").read_text())
    validate_demo(demo)
    scene = load_scene(scene_id)
    assert scene["steps"][0]["id"] == "ready"
    assert scene["steps"][0]["changes"] == {}
    assert scene["steps"][0]["active"] == []
    assert all(node.get("initial") for node in scene["nodes"])
    trace = snapshots(scene)
    initial = trace["ready"][0]
    assert initial["appState"] == "TASK_RUNNING，用户态执行"
    assert initial["mpState"] == "RUNNABLE"
    assert initial["timerPending"] == "0"
    assert initial["syscallResult"] == "尚未调用"
    assert {fields["kvmRun"] for fields, _ in trace.values()} == {
        "调用中，未返回 QEMU 用户态",
    }


@pytest.mark.parametrize("scene_id", TIMER_SCENES)
def test_timer_path_declares_clockevent_and_software_timer_assumptions(scene_id):
    scene = load_scene(scene_id)
    conditions = " ".join(scene["assumptions"])
    for term in ("单 vCPU", "Linux 6.18", "QEMU 10.2.0", "非 PREEMPT_RT",
                 "nanosleep", "timer slack", "TSC-deadline", "软件 hrtimer",
                 "APICv", "posted timer", "interrupt shadow"):
        assert term in conditions
    assert "无信号打断" in conditions
    assert "定时器提前量取 0" in conditions or "提前量为 0" in conditions
    if scene_id == "sleep-idle-vcpu":
        assert "HLT" in conditions
        assert "halt-poll 预算为 0" in conditions
    assert all(step["sourceIds"] for step in scene["steps"])


@pytest.mark.parametrize("scene_id", TIMER_SCENES)
def test_host_timer_and_guest_wakeup_are_separate_events(scene_id):
    trace = snapshots(load_scene(scene_id))
    assert trace["arm-deadline"][0]["timerPending"] == "0"
    assert trace["timer-expire"][0]["timerPending"] == "1"
    assert trace["stage-vector"][0]["timerPending"] == "0"
    assert trace["stage-vector"][0]["vector"] == "LAPIC 中有待递送的定时向量"
    for step in ("timer-expire", "stage-vector", "guest-timer"):
        assert trace[step][0]["appState"] == BLOCKED
        assert trace[step][0]["sleeperTask"] == "A"
        assert trace[step][0]["syscallResult"] == "尚未返回"
    assert trace["task-ready"][0]["appState"] == READY
    assert trace["task-ready"][0]["sleeperTask"] == "NULL（到期回调已执行）"
    assert "入口已完成 APIC EOI" in trace["guest-timer"][0]["vector"]


@pytest.mark.parametrize("scene_id", TIMER_SCENES)
def test_application_returns_only_after_guest_dispatch(scene_id):
    scene = load_scene(scene_id)
    trace = snapshots(scene)
    order = [step["id"] for step in scene["steps"]]
    assert order.index("task-ready") < order.index("guest-dispatch")
    assert order.index("guest-dispatch") < order.index("return-app")
    assert trace["guest-dispatch"][0]["appState"] == "TASK_RUNNING，正在 Guest 内核执行"
    assert all(fields["syscallResult"] != "0（休眠完成）"
               for key, (fields, _) in trace.items() if key != "return-app")
    assert trace["return-app"][0]["syscallResult"] == "0（休眠完成）"
    assert trace["return-app"][0]["guestCurrent"] == "任务 A"
    assert trace["return-app"][0]["sleeperTask"] == "本次栈上等待对象已结束"


def test_runnable_peer_and_host_preemption_do_not_halt_vcpu():
    scene = load_scene("sleep-other-task")
    trace = snapshots(scene)
    assert len(scene["steps"]) == 12
    assert {fields["mpState"] for fields, _ in trace.values()} == {"RUNNABLE"}
    assert all(fields["hostState"].startswith("TASK_RUNNING")
               for fields, _ in trace.values())
    assert trace["run-other"][0]["guestCurrent"] == "任务 B"
    assert trace["run-other"][0]["appState"] == BLOCKED
    assert trace["host-preempt"][0]["hostState"] == "TASK_RUNNING，被抢占后等待物理 CPU"
    assert "任务 B" in trace["host-preempt"][0]["guestCurrent"]
    assert trace["host-resume"][0]["guestCurrent"] == "任务 B"
    order = [step["id"] for step in scene["steps"]]
    assert order.index("host-resume") < order.index("timer-expire")


def test_idle_path_separates_guest_halt_host_sleep_and_host_dispatch():
    scene = load_scene("sleep-idle-vcpu")
    trace = snapshots(scene)
    assert len(scene["steps"]) == 15
    assert trace["guest-idle"][0]["mpState"] == "RUNNABLE"
    assert trace["halt-exit"][0]["mpState"] == "HALTED"
    assert trace["halt-exit"][0]["hostState"] == "TASK_RUNNING，正在执行"
    assert trace["prepare-block"][0]["hostState"] == (
        "TASK_INTERRUPTIBLE，已发布等待者但仍在执行"
    )
    assert trace["host-sleep"][0]["hostState"] == "TASK_INTERRUPTIBLE，已睡眠"
    assert trace["host-ready"][0]["hostState"] == "TASK_RUNNING，已唤醒但等待物理 CPU"
    for step in ("host-ready", "host-dispatch", "stage-vector"):
        assert trace[step][0]["mpState"] == "HALTED"
        assert trace[step][0]["appState"] == BLOCKED
    assert trace["guest-timer"][0]["mpState"] == "RUNNABLE"


@pytest.mark.parametrize("scene_id", TIMER_SCENES)
def test_node_labels_do_not_lag_behind_delivered_timer_or_guest_schedule(scene_id):
    trace = snapshots(load_scene(scene_id))
    assert trace["guest-timer"][1]["kvm"] == "定时中断已递送，正在执行 Guest"
    assert trace["task-ready"][1]["guest-timer"] == "本次到期回调已完成"
    assert trace["return-app"][1]["guest-scheduler"] == "已选择 A"
    assert "待递送" not in trace["return-app"][1]["kvm"]
    if scene_id == "sleep-other-task":
        assert "等待恢复" in trace["timer-expire"][1]["peer"]
        assert "计算暂停" in trace["guest-timer"][1]["peer"]
    else:
        assert trace["guest-idle"][1]["guest-scheduler"] == "当前选择 idle"


def test_cpu_article_explains_timer_boundaries_and_reuses_existing_deep_dives():
    article = (ROOT / "virt_report" / "render" / "templates"
               / "_cpu_execution_article.html").read_text()
    for term in ("任务休眠与两层调度", "nanosleep", "hrtimer_sleeper",
                 "hrtimer_wakeup", "timer slack", "mp_state", "HALTED",
                 "宿主运行队列等待", "Guest 调度", "kvm-vcpu-halt.html",
                 "kvm-virtual-time.html"):
        assert term in article
    assert "五个小例子" not in article
