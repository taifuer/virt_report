"""Offline semantic checks for the lifecycle lab embedded in the startup article."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from virt_report.execution_demos import validate_demo


ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = ROOT / "virt_report/content/flows/vm-lifecycle.json"
TEMPLATE_PATH = ROOT / "virt_report/render/templates/_vm_lifecycle_lab.html"
BASELINES = {
    "libvirt": "4d937284669ecf180abea087d0b3ad67d9476349",
    "qemu": "698104725efad4b29079d857dfdebbd804e34c99",
    "linux": "7d0a66e4bb9081d75c82ec4957c50034cb0ea449",
}


@pytest.fixture(scope="module")
def demo():
    return json.loads(FLOW_PATH.read_text(encoding="utf-8"))


def scene(demo, scenario_id):
    return next(item for item in demo["scenarios"] if item["id"] == scenario_id)


def step(scene, step_id):
    return next(item for item in scene["steps"] if item["id"] == step_id)


def state_at(scene, step_id):
    values = {
        field["key"]: field["initial"]
        for structure in scene["structures"]
        for field in structure["fields"]
    }
    for item in scene["steps"]:
        values.update(item["changes"])
        if item["id"] == step_id:
            return values
    raise AssertionError(f"Missing lifecycle step: {step_id}")


def assert_order(scene, *ids):
    ordered = [item["id"] for item in scene["steps"]]
    positions = [ordered.index(step_id) for step_id in ids]
    assert positions == sorted(positions)
    assert len(positions) == len(set(positions))


def test_lifecycle_data_uses_shared_contract(demo):
    validate_demo(demo)
    assert demo["defaultScenario"] == "cold-start"
    assert {item["id"] for item in demo["scenarios"]} == {
        "cold-start", "acpi-shutdown", "force-stop", "pause-resume",
    }
    for item in demo["scenarios"]:
        assert 2 <= len(item["nodes"]) <= 8
        assert 2 <= len(item["steps"]) <= 20
        assert all(node.get("initial") for node in item["nodes"])
        initial = item["steps"][0]
        assert not initial["active"] and not initial["links"]
        assert not initial["changes"] and not initial["nodeStates"]
        assert all("？" not in point["title"] for point in item["steps"])


def test_lifecycle_sources_are_fixed_baselines_and_official_docs(demo):
    for source in demo["sources"].values():
        url = urlsplit(source["url"])
        assert url.scheme == "https"
        if url.netloc == "github.com":
            _, owner, repository, kind, commit, *_ = url.path.split("/")
            assert owner in {"libvirt", "qemu", "torvalds"}
            assert kind == "blob"
            assert commit == BASELINES[repository]
        else:
            assert url.netloc == "www.libvirt.org"
    for item in demo["scenarios"]:
        assert "Libvirt 12.0.0" in item["assumptions"][0]
        assert "QEMU 10.2.0" in item["assumptions"][0]
        assert "Linux 6.18" in item["assumptions"][0]


def test_cold_start_crosses_process_execution_and_readiness_boundaries(demo):
    cold = scene(demo, "cold-start")
    assert_order(cold, "start-job", "prepare-host", "launch-qemu", "create-vm",
                 "create-vcpus", "map-ram", "realize-devices", "configure-qmp",
                 "start-cpus", "boot-firmware", "boot-kernel", "probe-virtio",
                 "application-ready")
    preparing = state_at(cold, "start-job")
    assert preparing["domainState"] == "PAUSED / STARTING_UP"
    assert preparing["processState"] == "无 QEMU 进程"
    hardware = state_at(cold, "realize-devices")
    assert "保持暂停" in hardware["vcpuState"]
    assert "Guest 队列尚未启用" in hardware["deviceState"]
    assert hardware["applicationState"] == "未启动"
    running = state_at(cold, "start-cpus")
    assert running["domainState"] == "RUNNING / BOOTED"
    assert "start 成功" in running["apiResult"]
    assert running["applicationState"] == "未启动"
    ready = state_at(cold, "application-ready")
    assert ready["domainState"] == running["domainState"]
    assert ready["vcpuState"] == "由 Guest SMP 启动与调度管理"
    assert ready["applicationState"] == "示例健康检查成功"


def test_ram_registration_does_not_claim_all_pages_are_resident(demo):
    cold = scene(demo, "cold-start")
    ram = step(cold, "map-ram")
    assert "内存槽" in ram["description"]
    assert "不代表 8 GiB 宿主页已经全部驻留" in ram["detail"]
    assert "KVM_CREATE_VCPU" in step(cold, "create-vcpus")["detail"]
    assert "DRIVER_OK" in step(cold, "probe-virtio")["description"]


def test_shutdown_api_acceptance_precedes_guest_poweroff(demo):
    shutdown = scene(demo, "acpi-shutdown")
    assert_order(shutdown, "request-powerdown", "deliver-button", "guest-shutdown",
                 "guest-poweroff", "shutdown-event", "exit-qemu", "release-instance", "shutoff")
    requested = state_at(shutdown, "request-powerdown")
    assert requested["domainState"] == "RUNNING"
    assert requested["guestState"] == "内核与驱动运行中"
    assert "未等待客户机停机" in requested["apiResult"]
    assert "忽略按钮" in step(shutdown, "deliver-button")["detail"]


def test_shutdown_preserves_qemu_until_libvirt_termination(demo):
    shutdown = scene(demo, "acpi-shutdown")
    powered_off = state_at(shutdown, "shutdown-event")
    assert powered_off["domainState"] == "SHUTDOWN"
    assert powered_off["guestState"] == "客户机已关闭"
    assert "进程仍存在" in powered_off["processState"]
    assert powered_off["vcpuState"] == "客户机执行已停止"
    assert step(shutdown, "shutdown-event")["nodeStates"]["guest"] == "客户机已关闭"
    assert "-no-shutdown" in step(shutdown, "shutdown-event")["description"]
    assert "on_poweroff=destroy" in " ".join(shutdown["assumptions"])
    stopped = state_at(shutdown, "shutoff")
    assert stopped["domainState"] == "SHUTOFF / SHUTDOWN"
    assert stopped["processState"] == "QEMU 进程已退出"


def test_destroy_uses_vmm_termination_not_guest_acpi_shutdown(demo):
    destroy = scene(demo, "force-stop")
    assert_order(destroy, "destroy-job", "signal-vmm", "quiesce-vmm",
                 "release-instance", "destroyed")
    signal = step(destroy, "signal-vmm")
    assert "先向 QEMU 发送 SIGTERM" in signal["description"]
    assert "等待后升级 SIGKILL" in signal["detail"]
    assert "--graceful" in signal["detail"]
    assert "Guest 并未获得正常关机机会" in signal["detail"]
    assert not {"request", "acpi", "guest-poweroff"} & {link["id"] for link in destroy["links"]}
    quiesced = state_at(destroy, "quiesce-vmm")
    assert quiesced["guestState"] == "客户机未执行正常关机"
    assert "不保证应用持久化" in quiesced["dataState"]
    assert "SIGKILL 路径也不会执行" in step(destroy, "quiesce-vmm")["detail"]
    stopped = state_at(destroy, "destroyed")
    assert stopped["domainState"] == "SHUTOFF / DESTROYED"
    assert stopped["apiResult"] == "destroy 成功"
    assert "应用持久化状态未知" in stopped["dataState"]


@pytest.mark.parametrize("scenario_id", ["acpi-shutdown", "force-stop"])
def test_stopping_releases_kvm_only_after_process_references_end(demo, scenario_id):
    stopping = scene(demo, scenario_id)
    released = state_at(stopping, "release-instance")
    assert released["processState"] == "QEMU 进程已退出"
    assert released["vcpuState"] == "执行线程已结束"
    assert "释放" in released["kvmObjects"]
    assert "销毁" in released["kvmObjects"]
    assert "释放" in released["ramState"]
    assert "引用" in step(stopping, "release-instance")["description"]


def test_pause_resume_retains_instance_and_does_not_claim_application_readiness(demo):
    pause = scene(demo, "pause-resume")
    assert_order(pause, "request-pause", "pause-cpus", "paused", "request-resume",
                 "resume-cpus", "continue-service")
    initial = state_at(pause, "initial")
    paused = state_at(pause, "paused")
    resumed = state_at(pause, "continue-service")
    assert paused["domainState"] == "PAUSED / USER"
    assert "同一 QEMU 进程存活" in paused["processState"]
    assert paused["vcpuState"] == "全部 vCPU 暂停"
    for key in ("kvmObjects", "ramState", "hostHandles", "persistentState"):
        assert initial[key] == paused[key] == resumed[key]
    assert "没有生成持久化保存文件" in step(pause, "paused")["detail"]
    assert "STOP 可先于块层排空和刷新发送" in step(pause, "paused")["detail"]
    assert step(pause, "resume-cpus")["nodeStates"]["qemu"] == "客户机已恢复，QMP 可用"
    assert resumed["domainState"] == "RUNNING / UNPAUSED"
    assert "业务可用性单独检查" in resumed["applicationState"]


def test_all_operations_preserve_persistent_definition_and_system_disk(demo):
    for item in demo["scenarios"]:
        initial = state_at(item, item["steps"][0]["id"])
        final = state_at(item, item["steps"][-1]["id"])
        assert "XML" in initial["persistentState"] and "系统盘" in initial["persistentState"]
        assert initial["persistentState"] == final["persistentState"]


def test_lifecycle_fragment_reuses_existing_interaction_component():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert '<section id="lifecycle"' in template
    assert "{% include '_execution_lab.html' %}" in template
    assert "<h1" not in template
    assert "data-execution-controls" not in template
    assert "<script" not in template
