"""Keep execution handoffs distinct from ordinary calls and return paths."""

import pytest

from virt_report.execution_demos import get_demo


DEMOS = (
    "network-io", "cpu-execution", "memory-access", "interrupt-delivery",
    "gpu-execution", "vm-lifecycle",
)


def _paths(demo_id: str, scenario_id: str) -> dict:
    demo = get_demo(demo_id, presentation=False)
    scene = next(scene for scene in demo["scenarios"] if scene["id"] == scenario_id)
    return {path["id"]: path for path in scene["callPaths"]}


@pytest.mark.parametrize("demo_id", DEMOS)
def test_context_entries_are_explicit_and_do_not_change_default_relation(demo_id):
    demo = get_demo(demo_id)
    for scene in demo["scenarios"]:
        for path in scene["callPaths"]:
            assert path.get("flow", "sequence") in {"call", "via", "sequence", "return"}
            if "entry" in path:
                assert path["entry"]["kind"] in {"notification", "async", "callback", "return"}
                assert path["entry"]["label"].strip()
            if path.get("entry", {}).get("kind") in {"notification", "async", "callback"}:
                assert path.get("flow", "sequence") == "sequence"


def test_network_returns_follow_original_application_stack_not_completion_calls():
    demo = get_demo("network-io")
    for scene in demo["scenarios"]:
        tx = "-tx" in scene["id"]
        paths = {path["id"]: path for path in scene["callPaths"]}
        path = paths["guest-send-return" if tx else "guest-receive-return"]
        assert path["flow"] == "return"
        assert path["entry"]["kind"] == "return"
        if tx:
            assert "不等待 TX 完成" in path["entry"]["label"]
        else:
            assert "原接收线程" in path["entry"]["label"]
            assert paths["guest-complete"]["entry"]["kind"] == "async"


def test_network_backends_keep_their_distinct_execution_entries():
    demo = get_demo("network-io")
    for scene in demo["scenarios"]:
        paths = {path["id"]: path for path in scene["callPaths"]}
        backend = scene["id"].split("-", 1)[0]
        if backend in {"dpdk", "vhost"}:
            assert paths[f"{backend}-backend"]["entry"]["kind"] == "async"
        elif backend == "qemu":
            expected = "notification" if "-tx" in scene["id"] else "callback"
            assert paths["qemu-backend"]["entry"]["kind"] == expected
        else:
            assert "entry" not in paths["vdpa-backend"]
        # Filling ring entries and checking notifications are sibling work.
        if "backend-complete" in paths:
            assert paths["backend-complete"].get("flow", "sequence") == "sequence"
    path = _paths("network-io", "qemu-tx-backpressure")["backend-complete"]
    assert path["entry"]["kind"] == "callback"


def test_syscall_has_calls_and_separate_guest_return_not_vmentry():
    paths = _paths("cpu-execution", "guest-syscall")
    assert paths["guest-dispatch"]["flow"] == "call"
    assert paths["guest-return"]["flow"] == "return"
    assert paths["guest-return"]["entry"]["kind"] == "return"
    assert "entry" not in paths["syscall-reference"]


@pytest.mark.parametrize("demo_id,path_id", [
    ("cpu-execution", "qemu-read"), ("memory-access", "qemu-device"),
])
def test_mmio_userspace_dispatch_is_entered_after_kvm_run_returns(demo_id, path_id):
    scene_id = "mmio-userspace" if demo_id == "cpu-execution" else "mmio-read"
    path = _paths(demo_id, scene_id)[path_id]
    assert path["entry"]["kind"] == "return"
    assert "KVM_RUN 返回" in path["entry"]["label"]
    assert path.get("flow", "sequence") == "sequence"


def test_gpu_scheduler_is_a_callback_not_a_synchronous_ioctl_child():
    for scene in ("vfio-upload", "vfio-readback"):
        paths = _paths("gpu-execution", scene)
        assert paths["guest-dispatch"]["entry"]["kind"] == "callback"
        assert "DRM 调度器" in paths["guest-dispatch"]["entry"]["label"]
        assert paths["completion"]["entry"]["kind"] == "notification"
        assert paths["guest-submit"].get("flow", "sequence") == "sequence"
    paths = _paths("gpu-execution", "mdev-copy")
    assert paths["host-schedule"]["entry"]["kind"] == "async"


def test_suppressed_irq_enters_existing_napi_poll_without_interrupt():
    paths = _paths("interrupt-delivery", "notify-suppressed")
    assert paths["guest-poll"]["entry"]["kind"] == "async"
    assert "没有 IRQ 通知" in paths["guest-poll"]["entry"]["label"]
    for scene in ("msix-software", "irqfd", "apicv-posted"):
        path = _paths("interrupt-delivery", scene)["path-3"]
        assert path["entry"]["kind"] == "notification"
        assert path.get("flow", "sequence") == "sequence"


def test_lifecycle_resume_status_is_an_asynchronous_qmp_event():
    path = _paths("vm-lifecycle", "cold-start")["resume-event"]
    assert path["entry"]["kind"] == "notification"
    assert "RESUME" in path["entry"]["label"]
