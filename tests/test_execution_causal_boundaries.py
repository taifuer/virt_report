"""Reviewed call order must preserve asynchronous and initialization boundaries."""
from __future__ import annotations

import pytest

from virt_report.execution_demos import get_demo


def _scene(demo_id: str, scenario_id: str) -> dict:
    return next(scene for scene in get_demo(demo_id)["scenarios"]
                if scene["id"] == scenario_id)


def _paths(scene: dict) -> dict:
    return {path["id"]: path for path in scene["callPaths"]}


@pytest.mark.parametrize("scenario_id,expected", [
    ("ept-missing", ["ept-exit", "kvm-page-fault", "ept-map", "backing-page"]),
    ("dirty-write-protect", ["ept-exit", "dirty-fault", "dirty-bitmap"]),
    ("mmio-kernel-lapic", ["ept-exit", "mmio-emulate", "kvm-mmio-read", "lapic-mmio-read"]),
    ("mmio-ioeventfd", ["mmio-emulate", "kvm-mmio-write", "ioeventfd-write"]),
])
def test_memory_exit_and_emulator_precede_the_selected_handler(scenario_id, expected):
    scene = _scene("memory-access", scenario_id)
    paths = [path for path in scene["callPaths"] if expected[-1] in path["sourceIds"]]
    assert len(paths) == 1
    assert paths[0]["sourceIds"] == expected


@pytest.mark.parametrize("scenario_id", ["ept-missing", "host-page-fault"])
def test_ept_installation_occurs_after_returning_from_backing_page_lookup(scenario_id):
    scene = _scene("memory-access", scenario_id)
    paths = _paths(scene)
    assert "backing-page" in paths["path-1"]["sourceIds"]
    assert paths["install-ept"]["sourceIds"] == ["ept-install"]
    assert "返回" in paths["install-ept"]["boundary"]
    mapping = next(step for step in scene["steps"] if step["id"] == "map")
    assert "ept-install" in mapping["sourceIds"]


def test_ioeventfd_does_not_order_backend_execution_after_vcpu_resume():
    scene = _scene("memory-access", "mmio-ioeventfd")
    steps = {step["id"]: step for step in scene["steps"]}
    assert steps["signal"]["changes"]["backend"] == "已通知；IOThread 独立处理队列"
    assert "没有固定先后" in steps["complete"]["detail"]
    assert "可并发推进" in _paths(scene)["path-0"]["boundary"]


def test_hlt_interrupt_is_recorded_before_kick_and_target_resumption():
    paths = _paths(_scene("cpu-execution", "hlt-wakeup"))
    assert paths["host-wait"]["sourceIds"] == ["halt", "block"]
    assert paths["host-wake"]["sourceIds"] == ["irq", "wake"]
    assert paths["host-wake"]["entry"]["kind"] == "notification"
    assert paths["host-resume"]["sourceIds"] == ["halt-resume", "irq-ack"]
    assert paths["host-resume"]["entry"]["kind"] == "async"


@pytest.mark.parametrize("scenario_id", ["sleep-other-task", "sleep-idle-vcpu"])
def test_timer_callbacks_keep_host_and_guest_scheduler_contexts_separate(scenario_id):
    scene = _scene("cpu-execution", scenario_id)
    paths = _paths(scene)
    assert paths["host-arm-timer"]["sourceIds"] == [
        "lapic-deadline", "lapic-software-timer",
    ]
    host_callback = paths["host-timer-expire"]
    assert host_callback["entry"]["kind"] == "callback"
    assert host_callback["sourceIds"][:3] == [
        "lapic-timer-callback", "lapic-expiration", "wake",
    ]
    assert "scheduler-switch" not in host_callback["sourceIds"]
    assert not any(source.startswith("guest-") for source in host_callback["sourceIds"])
    assert paths["host-timer-delivery"]["entry"]["kind"] == "async"
    guest_callback = paths["guest-timer-callback"]
    assert guest_callback["entry"]["kind"] == "notification"
    assert guest_callback["sourceIds"] == [
        "guest-timer-vector", "guest-timer-queues", "guest-timer-wake", "scheduler-wake",
    ]
    assert paths["guest-task-resume"]["sourceIds"] == ["scheduler-switch", "guest-nanosleep"]
    assert paths["guest-task-resume"]["entry"]["kind"] == "async"


def test_cold_start_prepares_ram_backing_before_board_vcpus_and_memslots():
    scene = _scene("vm-lifecycle", "cold-start")
    ordered = [step["id"] for step in scene["steps"]]
    expected = ["create-vm", "prepare-ram", "create-vcpus", "map-ram", "realize-devices"]
    positions = [ordered.index(step_id) for step_id in expected]
    assert positions == sorted(positions)
    steps = {step["id"]: step for step in scene["steps"]}
    backing = steps["prepare-ram"]
    assert backing["sourceIds"] == ["qemu-memory-backend"]
    assert "内存槽待注册" in backing["changes"]["ramState"]
    assert "kvm" not in backing["active"]
    assert "RAM 后备" in steps["map-ram"]["description"]
    assert steps["realize-devices"]["sourceIds"] == ["qemu-board", "qemu-cli-devices"]


@pytest.mark.parametrize("scenario_id,step_id", [
    ("cold-start", "start-cpus"), ("pause-resume", "resume-cpus"),
])
def test_resume_event_delivery_and_actual_execution_can_interleave(scenario_id, step_id):
    scene = _scene("vm-lifecycle", scenario_id)
    step = next(step for step in scene["steps"] if step["id"] == step_id)
    assert "RESUME" in step["detail"]
    assert "交错" in step["detail"]
    assert "qemu-vm-start" in {
        source for path in scene["callPaths"] for source in path["sourceIds"]
    }
