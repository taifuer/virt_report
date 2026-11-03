"""The irqfd comparison changes the CPU delivery path, not the backend sender."""
import pytest

from virt_report.execution_demos import get_demo


def scene(identifier):
    return next(item for item in get_demo("interrupt-delivery")["scenarios"]
                if item["id"] == identifier)


def states(item):
    current = {field["key"]: field["initial"] for structure in item["structures"]
               for field in structure["fields"]}
    result = {}
    for step in item["steps"]:
        current.update(step["changes"])
        result[step["id"]] = dict(current)
    return result


@pytest.mark.parametrize("identifier", ["irqfd", "apicv-posted"])
def test_dpdk_data_and_notification_do_not_execute_qemu_per_request(identifier):
    demo = get_demo("interrupt-delivery")
    item = scene(identifier)
    steps = {step["id"]: step for step in item["steps"]}
    assert steps["write-data"]["sourceIds"] == ["dpdk-rx"]
    assert steps["used-fill"]["sourceIds"] == ["dpdk-used-fill"]
    assert steps["used-publish"]["sourceIds"] == ["dpdk-used-publish"]
    assert steps["notify-check"]["sourceIds"] == ["dpdk-notify-check"]
    assert steps["signal"]["sourceIds"] == ["dpdk-notify", "dpdk-callfd", "irqfd"]
    assert "eventfd_write(vq->callfd, 1)" in steps["signal"]["description"]
    assert "系统调用内" in steps["signal"]["detail"]
    assert "QEMU" not in {demo["sources"][key]["project"]
                          for step in item["steps"] for key in step["sourceIds"]}
    setup = next(path for path in item["callPaths"] if path["id"] == "setup")
    assert setup["sourceIds"] == ["bind", "vhost-call-setup"]
    assert "VHOST_USER_SET_VRING_CALL" in setup["boundary"]
    assert "不在每次" in setup["boundary"]


def test_posted_and_software_injection_share_the_same_completion_event():
    software, posted = scene("irqfd"), scene("apicv-posted")
    a, b = states(software), states(posted)
    for step_id in ("ready", "write-data", "used-fill", "used-publish", "notify-check", "signal"):
        for field in ("payload", "used-entry", "used", "last-used", "notify-decision", "event"):
            assert a[step_id][field] == b[step_id][field]
    assert a["signal"]["used"] == b["signal"]["used"] == "8"
    assert a["signal"]["last-used"] == b["signal"]["last-used"] == "7"
    assert a["signal"]["irr"] == "0" and b["signal"]["pir"] == "0"
    assert a["pending"]["irr"] == "1" and b["post"]["pir"] == "1"
    assert b["post"]["virr"] == "0" and b["sync"]["virr"] == "1"
    for item in (software, posted):
        assert sum("dpdk-callfd" in step["sourceIds"] for step in item["steps"]) == 1


def test_irqfd_fast_path_does_not_claim_kvm_set_msi_ioctl_path():
    for identifier in ("irqfd", "apicv-posted"):
        item = scene(identifier)
        runtime = {key for step in item["steps"] for key in step["sourceIds"]}
        assert "irq-inatomic" in runtime
        assert runtime.isdisjoint({"msix", "msi-entry", "msi-ioctl", "routing"})
    posted = scene("apicv-posted")
    assert next(step for step in posted["steps"] if step["id"] == "post")["sourceIds"] == ["posted"]
    assert "vtd-posting" not in {key for step in posted["steps"] for key in step["sourceIds"]}


def test_dpdk_source_anchors_are_pinned_to_the_reviewed_2411_functions():
    sources = get_demo("interrupt-delivery")["sources"]
    expected = {
        "dpdk-enqueue": ("lib/vhost/virtio_net.c", 1743, "rte_vhost_enqueue_burst()"),
        "dpdk-rx": ("lib/vhost/virtio_net.c", 1393, "virtio_dev_rx_split()"),
        "dpdk-used-fill": ("lib/vhost/virtio_net.c", 254, "do_flush_shadow_used_ring_split()"),
        "dpdk-used-publish": ("lib/vhost/virtio_net.c", 267, "flush_shadow_used_ring_split()"),
        "dpdk-notify-check": ("lib/vhost/vhost.h", 989, "vhost_vring_call_split()"),
        "dpdk-notify": ("lib/vhost/vhost.h", 950, "vhost_vring_inject_irq()"),
        "dpdk-callfd": ("lib/vhost/vhost_user.c", 3493, "vhost_user_inject_irq()"),
    }
    for key, signature in expected.items():
        source = sources[key]
        assert (source["path"], source["line"], source["symbol"]) == signature
        assert source["revision"] == "1b3bf1128d9bda5595861814792f74b8f57160c8"
        assert source["url"].endswith(f"/{signature[0]}#L{signature[1]}")


def test_software_posting_has_an_eventfd_node_outside_the_qemu_component():
    item = scene("apicv-posted")
    assert next(link for link in item["links"] if link["id"] == "signal")["to"] == "eventfd"
    route = next(link for link in item["links"] if link["id"] == "submit-msi")
    assert (route["from"], route["to"]) == ("eventfd", "source")
    assert "KVM_SIGNAL_MSI" not in route["label"]
    host = next(group for group in item["layout"]["domains"] if group["component"] == "host-kernel")
    assert host["nodes"] == ["eventfd"]
