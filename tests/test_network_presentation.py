"""A single teaching trace keeps the actual ownership boundaries intact."""
from copy import deepcopy

import pytest

from virt_report.execution_demos import get_demo, validate_demo, validate_selector


def test_published_network_choices_are_only_backend_and_direction():
    demo = get_demo("network-io")
    validate_demo(demo)
    validate_selector(demo["selector"], {scene["id"] for scene in demo["scenarios"]})
    assert {scene["id"] for scene in demo["scenarios"]} == {
        f"{backend}-{direction}"
        for backend in ("qemu", "vhost", "dpdk", "vdpa") for direction in ("tx", "rx")
    }
    for category in demo["selector"]["categories"]:
        for group in category["groups"]:
            assert len(group["scenarios"]) == 1
    assert all("parallel" not in scene for scene in demo["scenarios"])


def test_tap_submits_to_host_before_publishing_used_without_a_late_forwarding_stage():
    for scene in get_demo("network-io")["scenarios"]:
        if scene["id"] not in {"qemu-tx", "vhost-tx"}:
            continue
        steps = {step["id"]: step for step in scene["steps"]}
        order = list(steps)
        assert order.index("transfer") < order.index("fill-used") < order.index("used") < order.index("reclaim")
        assert "egress" not in steps
        assert "物理网卡发完或远端收到" in steps["transfer"]["detail"]
        assert steps["transfer"]["queueState"]["used"]["index"] == 0
        assert steps["used"]["queueState"]["used"]["index"] == 1


def test_independent_host_events_do_not_import_future_guest_reclamation():
    scenes = {scene["id"]: scene for scene in get_demo("network-io")["scenarios"]}
    for identifier, event in (("dpdk-tx", "pmd"), ("dpdk-rx", "free-mbuf")):
        steps = {step["id"]: step for step in scenes[identifier]["steps"]}
        order = list(steps)
        assert order.index("route-interrupt") < order.index(event) < order.index("prepare-interrupt")
        before = deepcopy(steps["route-interrupt"]["queueState"])
        actual = deepcopy(steps[event]["queueState"])
        before.pop("changed"); actual.pop("changed")
        assert actual == before
        assert actual["used"]["consumer"] == 0
    # The source catalog remains unchanged for exceptional-path regression tests.
    reference = get_demo("network-io", presentation=False)
    assert len(reference["scenarios"]) == 10
    assert any("parallel" in scene for scene in reference["scenarios"])


def replay(scene):
    values = {field["key"]: field["initial"]
              for structure in scene["structures"] for field in structure["fields"]}
    snapshots = {}
    for step in scene["steps"]:
        values.update(step["changes"])
        snapshots[step["id"]] = dict(values)
    return snapshots


@pytest.mark.parametrize("backend", ("qemu", "vhost", "dpdk", "vdpa"))
def test_rx_arrives_and_queues_independently_of_application_receive(backend):
    scene = next(s for s in get_demo("network-io")["scenarios"] if s["id"] == f"{backend}-rx")
    steps = {step["id"]: step for step in scene["steps"]}
    order = list(steps)
    events = ["prepare-buffer", "publish-buffer", "arrive", "used", "receive",
              "socket-enqueue", "recvfrom-wait", "copy-to-user", "recvfrom-return"]
    assert [order.index(event) for event in events] == sorted(order.index(event) for event in events)
    states = replay(scene)
    assert states["arrive"]["applicationCall"] == "尚未调用"
    assert states["socket-enqueue"]["applicationCall"] == "尚未调用"
    assert "1 个 UDP 数据报" in states["recvfrom-wait"]["socketBuffers"]
    assert "准备取得并复制" in states["recvfrom-wait"]["applicationResult"]
    assert "application" not in steps["arrive"]["active"]
    assert "application" not in steps["socket-enqueue"]["active"]
    assert "socket-ready" not in steps["socket-enqueue"]["links"]
    # Moving the old waiting step cannot reset posted descriptors or old data.
    before = deepcopy(steps["socket-enqueue"]["queueState"])
    after = deepcopy(steps["recvfrom-wait"]["queueState"])
    before.pop("changed")
    after.pop("changed")
    assert after == before
    assert after["used"]["consumer"] == 1
    assert after["buffers"][0]["state"] == "socket"
    paths = {path["id"]: path for path in scene["callPaths"]}
    assert list(paths).index("guest-network-input") < list(paths).index("guest-application")
    assert "等待" not in paths["guest-receive-return"]["boundary"]


def test_completion_boundaries_are_independent_and_have_one_notification_start():
    for scene in get_demo("network-io")["scenarios"]:
        values = replay(scene)
        assert len(scene["completion"]) == 4
        assert len({item["key"] for item in scene["completion"]}) == 4
        assert values["used"]["interruptBoundary"] == "尚未发起"
        assert values["notify"]["interruptBoundary"] == "已发起 · 尚未进入 Guest"
        assert values["route-interrupt"]["interruptBoundary"] == "vLAPIC 待处理 · 尚未注入"
        assert values["guest-interrupt"]["interruptBoundary"] == "Guest 已接受 · NAPI 已安排"
        assert sum(step["changes"].get("interruptBoundary") == "已发起 · 尚未进入 Guest"
                   for step in scene["steps"]) == 1
        assert all("队列完成与通知" != phase["title"] for phase in scene["phases"])
        if scene["id"].endswith("-tx"):
            assert values["sendto-return"]["queueBoundary"] == "已提交 · 后端仍可读取"
            assert values["used"]["queueBoundary"] == "used 已发布 · 可回收"
            assert values["reclaim"]["queueBoundary"] == "驱动已回收 · 可复用"
            assert all(state["delivery"] == "远端是否收到：未确认" for state in values.values())
        else:
            assert values["used"]["applicationBoundary"] == "尚未调用"
            assert values["recvfrom-return"]["applicationBoundary"] == "返回 18 · 应用取得载荷"
    dpdk = next(s for s in get_demo("network-io")["scenarios"] if s["id"] == "dpdk-tx")
    values = replay(dpdk)
    assert values["used"]["transportBoundary"] == "独立副本 · 尚未提交 PMD"
    assert values["pmd"]["interruptBoundary"] == values["route-interrupt"]["interruptBoundary"]
    assert values["reclaim"]["transportBoundary"] == "PMD 已接受 · 等待网卡完成"
    pmd = next(step for step in dpdk["steps"] if step["id"] == "pmd")
    assert pmd["nodeStates"]["backend"] == values["pmd"]["backendState"]
    assert "已交给 PMD" in values["pmd"]["backendState"]


@pytest.mark.parametrize("invalid", ("unknown-field", "duplicate-field", "empty-label", "too-many"))
def test_completion_metadata_is_validated(invalid):
    demo = deepcopy(get_demo("network-io"))
    items = demo["scenarios"][0]["completion"]
    if invalid == "unknown-field":
        items[0]["key"] = "missing"
    elif invalid == "duplicate-field":
        items[0]["key"] = items[1]["key"]
    elif invalid == "empty-label":
        items[0]["label"] = " "
    else:
        items.append(dict(items[0]))
    with pytest.raises(ValueError, match="completion"):
        validate_demo(demo)
