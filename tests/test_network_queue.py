"""Typed network queue causality and function-path regressions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from virt_report.execution_demos import validate_demo
from virt_report.network_queue import FUNCTIONS, RX_CAPACITY, enrich_network_demo


CONTENT = Path(__file__).parents[1] / "virt_report/content/flows/network-io.json"


@pytest.fixture()
def demo():
    content = json.loads(CONTENT.read_text(encoding="utf-8"))
    enrich_network_demo(content)
    return content


def scene(demo, identifier):
    return next(item for item in demo["scenarios"] if item["id"] == identifier)


def state(scene, step):
    return next(item["queueState"] for item in scene["steps"] if item["id"] == step)


def test_enriched_network_validates_and_remains_idempotent(demo):
    validate_demo(demo)
    original = deepcopy(demo)
    enrich_network_demo(demo)
    assert demo == original
    assert len(demo["scenarios"]) == 10
    assert all(18 <= len(item["steps"]) <= 24 for item in demo["scenarios"])
    with pytest.raises(ValueError):
        enrich_network_demo({"id": "cpu-execution"})


@pytest.mark.parametrize("backend", ["qemu", "vhost", "dpdk", "vdpa"])
def test_tx_typed_descriptors_and_two_publication_boundaries(demo, backend):
    item = scene(demo, f"{backend}-tx")
    initial = state(item, "initial")
    assert initial["freeDescriptors"] == 8 and not initial["descriptors"]
    prepared = state(item, "prepare")
    descriptors = prepared["descriptors"]
    assert [entry["index"] for entry in descriptors] == [0, 2]
    assert [entry["len"] for entry in descriptors] == [12, 60]
    assert descriptors[0]["next"] == 2 and descriptors[1]["next"] is None
    assert descriptors[0]["flags"] == ["NEXT"] and not descriptors[1]["flags"]
    assert all(not entry["writable"] for entry in prepared["buffers"])
    assert prepared["freeDescriptors"] == 6
    filled = state(item, "fill-avail")
    assert filled["avail"]["slots"][0] == 0 and filled["avail"]["index"] == 0
    assert filled["ownership"] == "driver"
    published = state(item, "publish")
    assert published["avail"]["index"] == 1 and published["ownership"] == "device"
    assert state(item, "consume")["avail"]["consumer"] == 1
    completion = state(item, "fill-used")
    assert completion["used"]["slots"][0] == {"id": 0, "len": 0}
    assert completion["used"]["index"] == 0 and completion["ownership"] == "device"
    assert state(item, "used")["used"]["index"] == 1
    reclaimed = state(item, "reclaim")
    assert reclaimed["used"]["consumer"] == 1 and reclaimed["freeDescriptors"] == 8
    assert reclaimed["descriptors"][0]["addr"] == descriptors[0]["addr"]
    assert reclaimed["descriptors"][0]["state"] == "reclaimed"
    assert reclaimed["avail"]["slots"][0] == 0


@pytest.mark.parametrize("backend", ["qemu", "vhost", "dpdk", "vdpa"])
def test_rx_capacity_actual_write_and_reuse_are_distinct(demo, backend):
    item = scene(demo, f"{backend}-rx")
    prepared = state(item, "prepare-buffer")
    assert prepared["descriptors"][0]["len"] == RX_CAPACITY == 1530
    assert prepared["descriptors"][0]["flags"] == ["WRITE"]
    assert prepared["buffers"][0]["validBytes"] == 0
    assert state(item, "fill-avail")["avail"]["index"] == 0
    assert state(item, "publish-buffer")["avail"]["index"] == 1
    written = state(item, "write-buffer")
    assert written["buffers"][0]["validBytes"] == 72 and written["used"]["index"] == 0
    filled = state(item, "fill-used")
    assert filled["used"]["slots"][0] == {"id": 2, "len": 72}
    assert filled["used"]["index"] == 0
    reclaimed = state(item, "receive")
    assert reclaimed["freeDescriptors"] == 8 and reclaimed["buffers"][0]["state"] == "napi"
    new = state(item, "refill-prepare")
    assert len(new["buffers"]) == 2 and new["buffers"][0]["validBytes"] == 72
    assert new["descriptors"][0]["addr"] != new["buffers"][0]["addr"]
    assert new["descriptors"][0]["addr"] == new["buffers"][1]["addr"]
    assert state(item, "refill-avail")["avail"]["slots"][:2] == [2, 2]
    assert state(item, "refill-avail")["avail"]["index"] == 1
    published = state(item, "refill")
    assert published["avail"]["index"] == 2 and published["used"]["index"] == 1
    assert published["avail"]["consumer"] == published["used"]["consumer"] == 1
    assert published["buffers"][0]["state"] == "napi"
    assert published["buffers"][1]["state"] == "device"
    assert published["used"]["slots"][0] == {"id": 2, "len": 72}


def test_network_stalls_do_not_advance_completion_or_write_unposted_memory(demo):
    tx = scene(demo, "qemu-tx-backpressure")
    for step in ("blocked", "retry"):
        snapshot = state(tx, step)
        assert snapshot["used"]["index"] == 0
        assert snapshot["used"]["slots"][0] is None
        assert snapshot["freeDescriptors"] == 6
        assert snapshot["ownership"] == "device"
    rx = scene(demo, "qemu-rx-no-buffer")
    empty = state(rx, "no-buffer")
    assert not empty["descriptors"] and not empty["buffers"]
    assert empty["avail"]["index"] == empty["used"]["index"] == 0
    assert state(rx, "replenish")["avail"]["index"] == 1
    assert state(rx, "retry")["avail"]["consumer"] == 1
    assert state(rx, "write-buffer")["buffers"][0]["validBytes"] == 72


def test_every_queue_snapshot_has_independent_typed_state(demo):
    for item in demo["scenarios"]:
        queue_structures = [structure for structure in item["structures"]
                            if structure["id"] == item["queue"]["structureId"]]
        assert len(queue_structures) == 1
        assert queue_structures[0]["id"] == "virtqueue"
        for step in item["steps"]:
            snapshot = step["queueState"]
            assert type(snapshot["freeDescriptors"]) is int
            for ring_name in ("avail", "used"):
                ring = snapshot[ring_name]
                assert len(ring["slots"]) == item["queue"]["size"] == 8
                assert 0 <= ring["consumer"] <= ring["index"] <= 2
            for descriptor in snapshot["descriptors"]:
                assert type(descriptor["addr"]) is type(descriptor["len"]) is int
                assert descriptor["len"] > 0
                assert set(descriptor["flags"]) <= {"NEXT", "WRITE"}
        item["steps"][-1]["queueState"]["avail"]["slots"][7] = 7
        assert item["steps"][0]["queueState"]["avail"]["slots"][7] is None


def test_backend_call_paths_have_actual_symbols_and_do_not_fake_hardware(demo):
    for key, (_, path, line, symbol) in FUNCTIONS.items():
        source = demo["sources"][key]
        assert source["symbol"] == symbol and source["path"] == path and source["line"] == line
        assert source["url"].endswith(f"/{path}#L{line}")
        assert len(source["revision"]) == 40
    for item in demo["scenarios"]:
        assert item["callPaths"][0]["id"] == "guest-application"
        assert any(path["id"] == "guest-complete" for path in item["callPaths"])
        represented = {source for path in item["callPaths"] for source in path["sourceIds"]}
        assert {source for step in item["steps"] for source in step["sourceIds"]} <= represented
        for source in represented:
            assert demo["sources"][source].get("symbol") or demo["sources"][source].get("kind") == "reference"
        for path in item["callPaths"]:
            assert path["context"] and path["boundary"] and path["sourceIds"]
            assert set(path["sourceIds"]) <= demo["sources"].keys()
        if item["id"].startswith("vdpa"):
            hardware = next(path for path in item["callPaths"] if path["id"] == "vdpa-backend")
            assert "不虚构" in hardware["boundary"]
        elif item["id"] == "qemu-rx":
            complete = next(path for path in item["callPaths"] if path["id"] == "backend-complete")
            assert "qemu-push" not in complete["sourceIds"]
        elif item["id"].startswith("vhost"):
            assert demo["sources"]["vhost-get-desc"]["symbol"] == "vhost_get_vq_desc_n()"


def test_application_call_paths_are_real_send_and_receive_functions(demo):
    assert demo["sources"]["guest-sendto"]["symbol"] == "__sys_sendto()"
    assert demo["sources"]["guest-udp-recv"]["symbol"] == "udp_recvmsg()"
    for item in demo["scenarios"]:
        paths = {path["id"]: path for path in item["callPaths"]}
        if "-tx" in item["id"]:
            assert "guest-copy-send" in paths["guest-application"]["sourceIds"]
            assert "不等待 TX used" in paths["guest-application"]["boundary"]
            assert "guest-send-return" in paths
        else:
            assert "guest-udp-wait" in paths["guest-application"]["sourceIds"]
            assert "guest-udp-enqueue" in paths["guest-network-input"]["sourceIds"]
            assert "guest-receive-return" in paths


def test_rx_small_buffers_use_context_getter_not_big_packet_or_tx_getter(demo):
    assert demo["sources"]["net-rq-get"]["symbol"] == "virtnet_rq_get_buf()"
    assert demo["sources"]["ring-get-ctx"]["symbol"] == "virtqueue_get_buf_ctx()"
    for item in demo["scenarios"]:
        path = next(path for path in item["callPaths"] if path["id"] == "guest-complete")
        if "-tx" in item["id"]:
            assert "ring-get" in path["sourceIds"]
            assert "net-rq-get" not in path["sourceIds"]
            continue
        assert path["sourceIds"] == [
            "net-rx-poll", "net-receive", "net-receive-packets", "net-rq-get",
            "ring-get-ctx", "ring-get-split", "net-receive-buf",
        ]
        receive = next(step for step in item["steps"] if step["id"] == "receive")
        assert {"net-rq-get", "ring-get-ctx"} <= set(receive["sourceIds"])
        assert "ring-get" not in receive["sourceIds"]


def test_published_completion_updates_buffer_ownership(demo):
    for item in demo["scenarios"]:
        steps = {step["id"]: step for step in item["steps"]}
        completed = steps["used"]["queueState"]
        assert completed["used"]["index"] == 1
        assert completed["used"]["consumer"] == 0
        assert all(buffer["state"] == "completed" for buffer in completed["buffers"])
        assert "buffers" in completed["changed"]
        reclaimed = steps["reclaim" if "-tx" in item["id"] else "receive"]["queueState"]
        assert all(buffer["state"] in {"reusable", "napi"} for buffer in reclaimed["buffers"])


def test_no_translated_text_is_parsed_to_build_queue(demo):
    original = json.loads(CONTENT.read_text(encoding="utf-8"))
    for item in original["scenarios"]:
        for step in item["steps"]:
            step["description"] = "translated description"
            step["detail"] = "translated explanation"
            for key in list(step["changes"]):
                step["changes"][key] = "translated field"
    enrich_network_demo(original)
    for changed, expected in zip(original["scenarios"], demo["scenarios"]):
        assert [step["queueState"] for step in changed["steps"]] == [step["queueState"] for step in expected["steps"]]
