"""Backend wakeups and IRQ delivery are separate from split-ring ownership."""
from copy import deepcopy

import pytest

from virt_report.execution_demos import get_demo


@pytest.fixture
def demo():
    return get_demo("network-io")


def scene(demo, identifier):
    return next(item for item in demo["scenarios"] if item["id"] == identifier)


def steps(item):
    return {step["id"]: step for step in item["steps"]}


def fields(item, last):
    state = {field["key"]: field["initial"]
             for structure in item["structures"] for field in structure["fields"]}
    for step in item["steps"]:
        state.update(step["changes"])
        if step["id"] == last:
            return state
    raise AssertionError(last)


def test_every_network_path_shows_publication_before_kick_and_consumption(demo):
    for item in demo["scenarios"]:
        by_id = steps(item)
        order = list(by_id)
        published = "publish" if "-tx" in item["id"] else (
            "replenish" if "no-buffer" in item["id"] else "publish-buffer")
        consumed = "consume" if "-tx" in item["id"] else (
            "retry" if "no-buffer" in item["id"] else "take-buffer")
        assert order.index(published) < order.index("kick") < order.index(consumed)
        assert by_id["kick"]["focus"] is False
        assert not by_id["kick"]["queueState"]["changed"]
        assert by_id["kick"]["queueState"]["avail"]["consumer"] == 0
        assert fields(item, published)["notification"] == "新索引已发布，尚未检查 kick 条件"


@pytest.mark.parametrize("backend", ["qemu", "vhost"])
@pytest.mark.parametrize("direction", ["tx", "rx"])
def test_event_driven_backends_have_a_real_kvm_kick_edge(demo, backend, direction):
    item = scene(demo, f"{backend}-{direction}")
    kick = steps(item)["kick"]
    links = {link["id"]: link for link in item["links"]}
    assert kick["active"] == ["guest", "kick", "backend"]
    assert kick["links"] == ["queue-doorbell", "backend-kick"]
    assert (links["queue-doorbell"]["from"], links["queue-doorbell"]["to"]) == ("guest", "kick")
    assert (links["backend-kick"]["from"], links["backend-kick"]["to"]) == ("kick", "backend")
    assert {"ring-kick", "ring-notify", "pci-notify", "kvm-ioeventfd"} <= set(kick["sourceIds"])
    kvm = next(domain for domain in item["layout"]["domains"] if domain["component"] == "kvm")
    assert kvm["nodes"] == ["kick", "interrupt", "vcpu"]


def test_dpdk_polling_does_not_invent_a_required_kick(demo):
    for direction in ("tx", "rx"):
        item = scene(demo, f"dpdk-{direction}")
        for step in item["steps"]:
            if step["id"] not in {"kick", "kick-refill"}:
                continue
            assert not step["links"]
            assert step["active"] == ["guest"]
            assert step["sourceIds"] == ["ring-kick"]
            assert "false" in step["detail"]
        assert not any(link["id"] == "queue-doorbell" for link in item["links"])
        assert "irqfd" in steps(item)["notify"]["description"]


def test_vdpa_direct_doorbell_and_software_completion_irq_are_not_conflated(demo):
    item = scene(demo, "vdpa-tx")
    by_id = steps(item)
    kick = by_id["kick"]
    assert kick["active"] == ["guest", "backend"]
    assert kick["links"] == ["backend-kick"]
    assert "host-notifier mmap" in " ".join(item["assumptions"])
    assert "后备路径" in kick["detail"]
    assert "kvm-ioeventfd" not in kick["sourceIds"]
    assert "宿主 vDPA 回调" in by_id["notify"]["description"]
    assert "kvm-irqfd" in by_id["notify"]["sourceIds"]


def test_rx_replenishment_checks_notification_again_without_assuming_each_refill_kicks(demo):
    no_buffer = scene(demo, "qemu-rx-no-buffer")
    assert steps(no_buffer)["kick"]["links"] == ["queue-doorbell", "backend-kick"]
    for identifier in ("qemu-rx", "qemu-rx-no-buffer", "vhost-rx"):
        item = scene(demo, identifier)
        by_id = steps(item)
        assert list(by_id).index("refill") < list(by_id).index("kick-refill")
        assert by_id["kick-refill"]["links"] == []
        assert "重新启用通知" in by_id["kick-refill"]["detail"]
        assert by_id["kick-refill"]["queueState"]["avail"]["index"] == 2


def test_completed_queue_and_interrupt_delivery_have_independent_progress(demo):
    for item in demo["scenarios"]:
        by_id = steps(item)
        order = list(by_id)
        reclaim = "reclaim" if "-tx" in item["id"] else "receive"
        required = ["used", "notify", "route-interrupt", "prepare-interrupt", "guest-interrupt", reclaim]
        assert [order.index(identifier) for identifier in required] == sorted(order.index(identifier) for identifier in required)
        assert fields(item, "used")["completionNotification"] == "尚未发送"
        assert fields(item, "notify")["lapicState"] == "无本次待处理中断"
        assert fields(item, "route-interrupt")["lapicState"] == "IRR：0x51 待处理"
        assert "尚未进入" in fields(item, "prepare-interrupt")["guestIrqState"]
        assert "VM-entry 成功" in fields(item, "guest-interrupt")["entryState"]
        assert fields(item, "guest-interrupt")["lapicState"] == "本次中断已由 Guest 应答（EOI）"
        assert by_id["guest-interrupt"]["nodeStates"]["interrupt"] == "Guest 已发送 EOI，ISR 中的 0x51 已清除"
        assert "尚未回收" in fields(item, "guest-interrupt")["guestIrqState"]
        for identifier in required[1:-1]:
            step = by_id[identifier]
            assert step["focus"] is False
            assert step["queueState"]["used"] == by_id["used"]["queueState"]["used"]
            assert step["queueState"]["used"]["consumer"] == 0
            assert not step["queueState"]["changed"]
        assert by_id[reclaim]["queueState"]["used"]["consumer"] == 1
        assert by_id["notify"]["active"] == ["backend", "interrupt"]
        assert by_id["prepare-interrupt"]["active"] == ["interrupt", "vcpu"]
        assert by_id["guest-interrupt"]["links"] == ["guest-interrupt"]


def test_qemu_main_loop_uses_direct_msi_while_accelerated_backends_use_irqfd(demo):
    for item in demo["scenarios"]:
        notification = steps(item)["notify"]
        if item["id"].startswith("qemu"):
            assert "qemu-msi-send" in notification["sourceIds"]
            assert "KVM_SIGNAL_MSI" in notification["description"]
            assert "kvm-irqfd" not in notification["sourceIds"]
            assert "kvm-msi-route" in steps(item)["route-interrupt"]["sourceIds"]
        else:
            assert "kvm-irqfd" in notification["sourceIds"]
            assert "qemu-msi-send" not in notification["sourceIds"]
            assert "kvm-msi-inatomic" in steps(item)["route-interrupt"]["sourceIds"]
        assert "未启用 APICv / posted interrupt" in " ".join(item["assumptions"])


def test_kick_and_irq_fields_reset_without_shared_mutation(demo):
    item = scene(demo, "qemu-tx")
    original = deepcopy(item)
    by_id = steps(item)
    by_id["guest-interrupt"]["nodeStates"]["vcpu"] = "changed"
    assert steps(original)["guest-interrupt"]["nodeStates"]["vcpu"] != "changed"
    assert not by_id["initial"]["active"]
    assert not by_id["initial"]["changes"]
    assert fields(item, "initial")["entryState"] == "尚未准备"
