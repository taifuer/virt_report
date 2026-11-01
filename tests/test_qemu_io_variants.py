"""Source and protocol contracts for QEMU SCSI and RBD scenario deltas."""
import copy
import json
import re
from pathlib import Path

import pytest

from virt_report import mechanisms


FLOW_DIR = Path(mechanisms.__file__).parent / "content" / "flows"


@pytest.fixture(scope="module")
def variants():
    return json.loads((FLOW_DIR / "qemu-io-variants.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def base():
    return json.loads((FLOW_DIR / "virtio-blk-read.json").read_text(encoding="utf-8"))


def _route(variants, identifier):
    return next(route for route in variants["routes"] if route["id"] == identifier)


def _step_references(base, route, operation):
    """Resolve only editorial references; runtime state validation belongs to the model."""
    steps = copy.deepcopy(base["steps"])
    deltas = []
    if operation == "write":
        deltas.append(base["operationVariants"]["write"])
    deltas.extend([route["common"], route["operations"][operation]])
    for delta in deltas:
        mapping = delta.get("sourceMap", {})
        for step in steps:
            step["sourceIds"] = [mapping.get(key, key) for key in step["sourceIds"]]
            step.update(copy.deepcopy(delta.get("stepPatches", {}).get(step["id"], {})))
        for item in delta.get("insertSteps", []):
            after = next(index for index, step in enumerate(steps) if step["id"] == item["after"])
            steps.insert(after + 1, copy.deepcopy(item["step"]))
        steps = [step for step in steps if step["id"] not in delta.get("removeSteps", [])]
    return steps


def test_variants_are_two_actual_backends_with_both_operations(variants):
    assert variants["schemaVersion"] == 1
    assert variants["id"] == "qemu-io-variants"
    assert [route["id"] for route in variants["routes"]] == ["local-scsi", "rbd-blk"]
    for route in variants["routes"]:
        assert set(route["operations"]) == {"read", "write"}
        assert route["common"]["assumptions"]
        assert route["common"]["insertSteps"]
        assert route["common"]["request"]["backend"] == route["id"]
        assert route["common"]["presentation"]["notificationMode"] == "eventfd"


@pytest.mark.parametrize("route_id", ["local-scsi", "rbd-blk"])
def test_added_sources_pin_reviewed_repositories_and_lines(base, variants, route_id):
    route = _route(variants, route_id)
    commits = {baseline["project"]: baseline["commit"] for baseline in base["baselines"]}
    repositories = {"QEMU": "qemu/qemu", "Linux": "torvalds/linux"}
    assert not set(route["sources"]).intersection(base["sources"])
    for identifier, source in route["sources"].items():
        assert re.fullmatch(r"(?:scsi|rbd)-[a-z0-9-]+", identifier)
        assert source["symbol"] and source["path"]
        assert type(source["line"]) is int and source["line"] > 0
        assert ".." not in source["path"].split("/")
        prefix = f'https://github.com/{repositories[source["project"]]}/blob/{commits[source["project"]]}'
        assert source["url"] == f'{prefix}/{source["path"]}#L{source["line"]}'


@pytest.mark.parametrize("route_id", ["local-scsi", "rbd-blk"])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_every_derived_step_has_appropriate_sources_and_callpath_coverage(base, variants, route_id, operation):
    route = _route(variants, route_id)
    sources = {**base["sources"], **route["sources"]}
    paths = route["operations"][operation]["callPaths"]
    assert len({path["id"] for path in paths}) == len(paths)
    path_sources = {identifier for path in paths for identifier in path["sourceIds"]}
    assert path_sources <= sources.keys()
    for path in paths:
        assert all(path[field] for field in ("title", "context", "sourceIds", "boundary"))
    steps = _step_references(base, route, operation)
    assert len({step["id"] for step in steps}) == len(steps)
    assert steps[0]["id"] == "application-ready" and steps[-1]["id"] == "return-to-user"
    for step in steps:
        assert set(step["sourceIds"]) <= sources.keys()
        assert set(step["sourceIds"]) & path_sources, step["id"]
    used_sources = path_sources | {identifier for step in steps for identifier in step["sourceIds"]}
    if route_id == "local-scsi":
        assert not any(sources[key]["path"].endswith(("virtio_blk.c", "virtio-blk.c")) for key in used_sources)
        assert all("VirtIOBlockReq" not in step["description"] + step["detail"] for step in steps)
        assert "VirtIOSCSIReq" in next(step for step in steps if step["id"] == "consume-chain")["description"]
    else:
        assert not any(sources[key]["path"] == "block/file-posix.c" for key in used_sources)


def test_scsi_layout_distinguishes_request_response_payload_and_reported_length(variants):
    route = _route(variants, "local-scsi")
    request = route["common"]["request"]
    assert request["headerBytes"] == 8 + 8 + 1 + 1 + 1 + 32 == 51
    assert request["responseBytes"] == 4 + 4 + 2 + 1 + 1 + 96 == 108
    assert request["usedLength"] == 4096 + 108 == 4204
    assert "4204" in request["usedLengthNote"] and "WRITE" not in request["usedLengthNote"]
    read = route["operations"]["read"]["request"]
    write = route["operations"]["write"]["request"]
    assert "WRITE" in write["usedLengthNote"] and "实际可写" in write["usedLengthNote"]
    assert read["command"] == "READ(10)" and write["command"] == "WRITE(10)"
    assert [item["len"] for item in read["descriptors"]] == [51, 108, 4096]
    assert [item["len"] for item in write["descriptors"]] == [51, 4096, 108]
    assert [item["flags"] for item in read["descriptors"]] == [["NEXT"], ["NEXT", "WRITE"], ["WRITE"]]
    assert [item["flags"] for item in write["descriptors"]] == [["NEXT"], ["NEXT"], ["WRITE"]]
    # QEMU 10.2 reports qsgl.size + response capacity even for a WRITE.
    # Do not silently replace this implementation fact with a protocol-sized 108.
    writable = sum(item["len"] for item in write["descriptors"] if "WRITE" in item["flags"])
    assert writable == 108 < request["usedLength"]
    assert route["common"]["stepPatches"]["write-used"]["changes"]["usedLength"] == 4204


def test_scsi_has_command_dispatch_and_response_decode_boundaries(base, variants):
    route = _route(variants, "local-scsi")
    ids = [step["id"] for step in _step_references(base, route, "read")]
    assert ids.index("ready") < ids.index("scsi-build-cdb") < ids.index("prepare-descriptors")
    assert ids.index("parse-read") < ids.index("scsi-dispatch-command") < ids.index("submit-read")
    assert ids.index("reclaim-chain") < ids.index("scsi-decode-response") < ids.index("complete-bio")
    assert "scsi-linux-end" in route["common"]["stepPatches"]["finish-request"]["sourceIds"]
    assert route["common"]["presentation"]["queueTitle"] == "request queue 2"


@pytest.mark.parametrize("operation", ["read", "write"])
def test_rbd_has_client_callback_and_bh_before_virtio_completion(base, variants, operation):
    route = _route(variants, "rbd-blk")
    steps = _step_references(base, route, operation)
    ids = [step["id"] for step in steps]
    order = ["submit-read", "rbd-client-work", "read-data", "rbd-completion-callback",
             "rbd-resume-coroutine", "write-status", "write-used"]
    assert [ids.index(identifier) for identifier in order] == sorted(ids.index(identifier) for identifier in order)
    submit = next(step for step in steps if step["id"] == "submit-read")
    assert f"rbd-aio-{operation}" in submit["sourceIds"]
    assert f"rbd-{operation}" in submit["sourceIds"]
    callback = next(step for step in steps if step["id"] == "rbd-completion-callback")
    assert callback["actor"] == "backend" and "非QEMU线程" in callback["detail"]
    resume = next(step for step in steps if step["id"] == "rbd-resume-coroutine")
    assert resume["actor"] == "qemu" and "rbd-bh" in resume["sourceIds"]
    assert "未使用宿主 krbd" in route["common"]["presentation"]["controlPlane"]
    assert route["operations"][operation]["request"]["usedLength"] == (4097 if operation == "read" else 1)
