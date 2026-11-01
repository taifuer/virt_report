"""Protocol and execution-domain contracts for the offline SPDK I/O traces."""
import json
from pathlib import Path
import re

import pytest

from virt_report.io_scenarios import build_io_scenarios


FLOWS = Path(__file__).resolve().parents[1] / "virt_report" / "content" / "flows"
SPDK_COMMIT = "a2f2a0b2794366fe1e42b97061782aae25119457"
REPOSITORIES = {"Linux": "torvalds/linux", "QEMU": "qemu/qemu", "SPDK": "spdk/spdk"}


@pytest.fixture(scope="module")
def spdk_routes():
    base = json.loads((FLOWS / "virtio-blk-read.json").read_text(encoding="utf-8"))
    catalog = build_io_scenarios(base, FLOWS)
    return {route["id"]: route for route in catalog["routes"] if route["id"].startswith("spdk-")}


@pytest.fixture(params=[("spdk-blk", "read"), ("spdk-blk", "write"),
                       ("spdk-scsi", "read"), ("spdk-scsi", "write")])
def scene(spdk_routes, request):
    backend, operation = request.param
    return spdk_routes[backend]["scenarios"][operation]


def _steps(scene):
    return {step["id"]: step for step in scene["steps"]}


def _referenced_sources(scene):
    return {key for entry in scene["steps"] + scene["callPaths"] for key in entry["sourceIds"]}


def test_each_spdk_trace_uses_pinned_sources_and_no_qemu_data_plane(scene):
    baselines = {item["project"]: item for item in scene["baselines"]}
    assert baselines["SPDK"]["commit"] == SPDK_COMMIT
    used = _referenced_sources(scene)
    assert used
    for identifier in used:
        source = scene["sources"][identifier]
        assert source["project"] in {"Linux", "SPDK"}, identifier
        assert re.fullmatch(r"[0-9a-f]{40}", baselines[source["project"]]["commit"])
        assert ".." not in source["path"].split("/")
        assert type(source["line"]) is int and source["line"] > 0
        expected = (f'https://github.com/{REPOSITORIES[source["project"]]}/blob/'
                    f'{baselines[source["project"]]["commit"]}/{source["path"]}#L{source["line"]}')
        assert source["url"] == expected
    assert not any(step["actor"] == "qemu" for step in scene["steps"])
    assert scene["presentation"]["deviceDomain"] == "SPDK"
    assert "QEMU" in scene["presentation"]["controlPlane"]
    assert "控制面" in scene["presentation"]["controlPlane"]
    assert "不消费" in scene["presentation"]["controlPlane"]


def test_polling_observes_available_without_submission_ioeventfd(scene):
    steps = _steps(scene)
    assert scene["presentation"]["notificationMode"] == "poll"
    assert not {"write-doorbell", "signal-ioeventfd", "dispatch-queue"}.intersection(steps)
    assert "NO_NOTIFY" in steps["check-kick"]["description"]
    poll = steps["spdk-poll-available"]
    assert poll["actor"] == "spdk"
    assert poll["changes"] == {"notified": True}
    assert poll["edge"]["from"] == "queue" and poll["edge"]["to"] == "device"
    assert "notify" not in poll["active"]
    assert list(steps).index("publish-available") < list(steps).index(poll["id"])
    assert list(steps).index(poll["id"]) < list(steps).index("consume-chain")
    assert not any("notify" in step["active"] for step in scene["steps"])


def test_nvme_submission_hardware_boundary_and_callback_are_distinct(scene):
    steps = _steps(scene)
    order = ["submit-read", "spdk-nvme-submit", "read-data", "spdk-nvme-poll-completion",
             "write-status", "write-used", "publish-used", "signal-irqfd"]
    assert [list(steps).index(key) for key in order] == sorted(list(steps).index(key) for key in order)
    operation = scene["request"]["operation"]
    submit = steps["spdk-nvme-submit"]
    callback = steps["spdk-nvme-poll-completion"]
    assert f"spdk-nvme-{operation}-command" in submit["sourceIds"]
    assert f"spdk_nvme_ns_cmd_{operation}_with_md" in submit["description"] + submit["detail"]
    assert f"spdk-nvme-{operation}-done" in callback["sourceIds"]
    assert steps["read-data"]["actor"] == "backend"
    assert "控制器" in steps["read-data"]["detail"]
    assert steps["read-data"]["changes"] == {}
    assert "尚未确认完成" in steps["read-data"]["description"]
    assert "下一步" in steps["read-data"]["detail"]
    assert callback["actor"] == "spdk" and callback["changes"]["dataReady"] is True
    # The internal marker is only success bookkeeping for SCSI; the UI renders
    # response/status/residual/sense separately rather than inventing a status byte.
    assert steps["write-status"]["changes"] == {"statusWritten": True, "statusByte": "0x00"}
    assert "callfd" in steps["signal-irqfd"]["description"]
    assert steps["signal-irqfd"]["actor"] == "spdk"
    assert steps["route-interrupt"]["actor"] == "kvm"
    assert steps["inject-interrupt"]["changes"]["irqInjected"] is True
    assert list(steps).index("inject-interrupt") < list(steps).index("guest-notice")


@pytest.mark.parametrize("backend,operation,expected", [
    ("spdk-blk", "read", 4097), ("spdk-blk", "write", 1),
    ("spdk-scsi", "read", 4204), ("spdk-scsi", "write", 108),
])
def test_used_length_matches_protocol_and_operation(spdk_routes, backend, operation, expected):
    scene = spdk_routes[backend]["scenarios"][operation]
    assert scene["request"]["usedLength"] == expected
    assert _steps(scene)["write-used"]["changes"]["usedLength"] == expected
    writable = sum(item["len"] for item in scene["request"]["descriptors"] if "WRITE" in item["flags"])
    assert expected == writable


@pytest.mark.parametrize("operation", ["read", "write"])
def test_scsi_command_response_layout_and_guest_completion_are_independent(spdk_routes, operation):
    scene = spdk_routes["spdk-scsi"]["scenarios"][operation]
    request = scene["request"]
    assert request["interface"] == "virtio-scsi"
    assert request["command"] == f"{operation.upper()}(10)"
    assert request["headerBytes"] == 51 and request["responseBytes"] == 108
    assert scene["presentation"]["queueTitle"] == "request queue 2"
    expected_lengths = [51, 108, 4096] if operation == "read" else [51, 4096, 108]
    assert [item["len"] for item in request["descriptors"]] == expected_lengths
    used = _referenced_sources(scene)
    assert not any(scene["sources"][key]["path"].endswith(("virtio_blk.c", "vhost_blk.c")) for key in used)
    steps = _steps(scene)
    assert "spdk-guest-scsi-response" in steps
    assert "spdk-guest-scsi-done" in steps["spdk-guest-scsi-response"]["sourceIds"]
    assert "spdk-scsi-response" in steps["write-status"]["sourceIds"]
    assert "response=OK" in steps["write-status"]["detail"]
    assert "spdk-guest-scsi-end" in steps["finish-request"]["sourceIds"]
    assert list(steps).index("reclaim-chain") < list(steps).index("spdk-guest-scsi-response")
    assert list(steps).index("spdk-guest-scsi-response") < list(steps).index("complete-bio")
