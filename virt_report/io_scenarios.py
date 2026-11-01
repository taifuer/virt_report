"""Build source-reviewed I/O playback catalogs offline, without a JS runtime."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re


_REPOSITORIES = {
    "Linux": "torvalds/linux", "QEMU": "qemu/qemu",
    "SPDK": "spdk/spdk", "Ceph": "ceph/ceph",
}
_ROUTES = ("local-blk", "local-scsi", "rbd-blk", "spdk-blk", "spdk-scsi")
_FILES = ("qemu-io-variants.json", "spdk-io-variants.json")
_NODES = {"application", "driver", "queue", "notify", "device", "storage",
          "buffer", "completion", "interrupt", "reclaim"}
_LANES = {"guest-user", "guest", "kvm", "qemu", "spdk", "ceph", "backend", "memory"}
_FLAGS = {
    "bufferReady", "syscallEntered", "pagesMapped", "blockSubmitted", "prepared",
    "published", "notified", "consumed", "submitted", "dataReady", "statusWritten",
    "usedWritten", "usedPublished", "interruptSignaled", "irqRouted", "irqPending",
    "irqInjected", "guestNoticed", "reclaimed", "bioCompleted", "completed", "userReturned",
}
_COUNTERS = {"availIndex", "backendIndex", "usedIndex", "driverIndex", "freeDescriptors"}
_STATE_KEYS = _FLAGS | _COUNTERS | {"statusByte", "usedId", "usedLength"}
_DEPENDENCIES = {
    "pagesMapped": "syscallEntered", "blockSubmitted": "pagesMapped",
    "prepared": "blockSubmitted", "published": "prepared", "notified": "published",
    "consumed": "notified", "submitted": "consumed", "dataReady": "submitted",
    "statusWritten": "dataReady", "usedWritten": "statusWritten",
    "usedPublished": "usedWritten", "interruptSignaled": "usedPublished",
    "irqRouted": "interruptSignaled", "irqPending": "irqRouted",
    "irqInjected": "irqPending", "guestNoticed": "irqInjected",
    "reclaimed": "guestNoticed", "bioCompleted": "reclaimed",
    "completed": "bioCompleted", "userReturned": "completed",
}
_STEP_PATCH_KEYS = {
    "title", "actor", "description", "detail", "sourceIds", "active", "edge", "changes",
}
_PATH_PATCH_KEYS = {"title", "context", "sourceIds", "boundary"}
_DELTA_KEYS = {
    "title", "summary", "request", "presentation", "lanes", "assumptions",
    "callPaths", "baselines", "sources", "sourceMap",
    "phasePatches", "callPathPatches", "stepPatches", "insertSteps", "removeSteps",
}


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"Invalid I/O scenario: {message}")


def _object(value: object, label: str) -> dict:
    _check(isinstance(value, dict), label)
    return value


def _text(value: object, label: str) -> None:
    _check(isinstance(value, str) and bool(value.strip()), label)


def _identifier(value: object, label: str) -> None:
    _check(isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]*", value)), label)


def _integer(value: object, label: str, minimum: int = 0) -> None:
    _check(type(value) is int and minimum <= value <= 2**53 - 1, label)


def _indexed(items: object, label: str) -> dict[str, dict]:
    _check(isinstance(items, list) and bool(items), label)
    result = {}
    for item in items:
        _object(item, label)
        key = item.get("id")
        _identifier(key, f"{label} id")
        _check(key not in result, f"duplicate {label} id")
        result[key] = item
    return result


def _patch(target: dict, patch: object, allowed: set[str], label: str) -> None:
    patch = _object(patch, label)
    _check(set(patch) <= allowed, f"unknown {label} field")
    # Nested values (especially changes/sourceIds) are replacements, not recursive merges.
    target.update(deepcopy(patch))


def _patch_by_id(items: list, patches: object, allowed: set[str], label: str) -> None:
    indexed = _indexed(items, label)
    for key, patch in _object(patches, label).items():
        _check(key in indexed, f"unknown {label} target: {key}")
        _patch(indexed[key], patch, allowed, label)


def _map_sources(scene: dict, mapping: object) -> None:
    for old, new in _object(mapping, "source map").items():
        _check(isinstance(new, str) and old in scene["sources"] and new in scene["sources"],
               "unknown source map reference")
    for item in scene["steps"] + scene["callPaths"]:
        item["sourceIds"] = [mapping.get(key, key) for key in item["sourceIds"]]


def _insert_steps(scene: dict, insertions: object) -> None:
    _check(isinstance(insertions, list), "insert steps")
    for insertion in insertions:
        _object(insertion, "step insertion")
        _check(set(insertion) in ({"after", "step"}, {"before", "step"}), "step insertion keys")
        anchor = "after" if "after" in insertion else "before"
        indexed = _indexed(scene["steps"], "steps")
        _check(insertion[anchor] in indexed, "unknown insertion anchor")
        added = _object(insertion["step"], "inserted step")
        _identifier(added.get("id"), "inserted step id")
        _check(added["id"] not in indexed, "duplicate inserted step")
        position = next(index for index, item in enumerate(scene["steps"])
                        if item["id"] == insertion[anchor])
        scene["steps"].insert(position + (anchor == "after"), deepcopy(added))


def _merge_evidence(scene: dict, delta: dict) -> None:
    if "baselines" in delta:
        _check(isinstance(delta["baselines"], list), "baseline delta")
        indexed = {item["project"]: item for item in scene["baselines"]}
        seen = set()
        for baseline in delta["baselines"]:
            baseline = _object(baseline, "baseline delta entry")
            project = baseline.get("project")
            _check(isinstance(project, str) and project not in seen, "duplicate baseline delta")
            seen.add(project)
            _check(project not in indexed or indexed[project] == baseline, "conflicting baseline")
            if project not in indexed:
                scene["baselines"].append(deepcopy(baseline))
                indexed[project] = baseline
    if "sources" in delta:
        for key, source in _object(delta["sources"], "source delta").items():
            _check(key not in scene["sources"] or scene["sources"][key] == source,
                   f"conflicting source: {key}")
            scene["sources"][key] = deepcopy(source)


def _apply_delta(scene: dict, delta: object) -> None:
    delta = _object(delta, "scenario delta")
    _check(set(delta) <= _DELTA_KEYS, "unknown scenario delta field")
    _merge_evidence(scene, delta)
    for key in ("title", "summary", "lanes", "assumptions", "callPaths"):
        if key in delta:
            scene[key] = deepcopy(delta[key])
    for key in ("request", "presentation"):
        if key in delta:
            scene.setdefault(key, {}).update(deepcopy(_object(delta[key], f"{key} delta")))
    _map_sources(scene, delta.get("sourceMap", {}))
    _patch_by_id(scene["phases"], delta.get("phasePatches", {}), {"title", "summary"}, "phase patch")
    _patch_by_id(scene["callPaths"], delta.get("callPathPatches", {}), _PATH_PATCH_KEYS, "call path patch")
    _patch_by_id(scene["steps"], delta.get("stepPatches", {}), _STEP_PATCH_KEYS, "step patch")
    _insert_steps(scene, delta.get("insertSteps", []))
    removed = delta.get("removeSteps", [])
    _check(isinstance(removed, list) and all(isinstance(key, str) for key in removed)
           and len(set(removed)) == len(removed), "remove steps")
    indexed = _indexed(scene["steps"], "steps")
    _check(all(key in indexed for key in removed), "unknown removed step")
    scene["steps"] = [item for item in scene["steps"] if item["id"] not in removed]


def _layout(request: dict) -> tuple[list[int], list[list[str]], int]:
    interface = request.get("interface", "virtio-blk")
    _check(interface in ("virtio-blk", "virtio-scsi"), "request interface")
    header = request.get("headerBytes", 51 if interface == "virtio-scsi" else 16)
    response = request.get("responseBytes", request.get(
        "completionBytes", 108 if interface == "virtio-scsi" else 1))
    for value in (header, response):
        _integer(value, "header/response bytes", 1)
        _check(value <= 65536, "header/response size limit")
    _check("completionBytes" not in request or request["completionBytes"] == response,
           "completion size alias mismatch")
    _check(interface != "virtio-blk" or (header, response) == (16, 1), "virtio-blk sizes")
    read = request["operation"] == "read"
    scsi_read = interface == "virtio-scsi" and read
    lengths = [header, response, request["bytes"]] if scsi_read else [header, request["bytes"], response]
    permissions = [["NEXT"], ["NEXT", "WRITE"] if read else ["NEXT"], ["WRITE"]]
    used = request.get("usedLength", response + (request["bytes"] if read else 0))
    _integer(used, "request used length")
    _check(used <= sum(lengths), "request used length exceeds descriptor capacity")
    return lengths, permissions, used


def _validate_scene(scene: dict) -> None:
    _object(scene, "scene")
    _identifier(scene.get("id"), "scene id")
    for key in ("title", "summary"):
        _text(scene.get(key), key)
    assumptions = scene.get("assumptions")
    _check(isinstance(assumptions, list) and bool(assumptions), "assumptions")
    for value in assumptions:
        _text(value, "assumption")
    baselines = scene.get("baselines")
    _check(isinstance(baselines, list) and 2 <= len(baselines) <= 4, "baselines")
    commits = {}
    for baseline in baselines:
        baseline = _object(baseline, "baseline")
        project, commit = baseline.get("project"), baseline.get("commit")
        _check(isinstance(project, str) and project in _REPOSITORIES and project not in commits,
               "baseline project")
        _check(isinstance(commit, str) and bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "baseline commit")
        _text(baseline.get("version"), "baseline version")
        _check(baseline.get("url") == f"https://github.com/{_REPOSITORIES[project]}/tree/{commit}",
               "baseline URL")
        commits[project] = commit
    _check("Linux" in commits, "Linux baseline")
    sources = _object(scene.get("sources"), "sources")
    _check(bool(sources), "empty sources")
    for key, source in sources.items():
        _identifier(key, "source id")
        source = _object(source, "source")
        project, path, line = source.get("project"), source.get("path"), source.get("line")
        _check(isinstance(project, str) and project in commits, "source project")
        _check(isinstance(path, str) and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", path))
               and not any(part in ("", ".", "..") for part in path.split("/")), "source path")
        _integer(line, "source line", 1)
        _text(source.get("symbol"), "source symbol")
        expected = f"https://github.com/{_REPOSITORIES[project]}/blob/{commits[project]}/{path}#L{line}"
        _check(source.get("url") == expected, "source URL")
    request = _object(scene.get("request"), "request")
    _check(request.get("operation") in ("read", "write"), "operation")
    _check(scene["id"].endswith(f'-{request["operation"]}'), "scene operation identity")
    for key, expected in (("id", "R1"), ("bytes", 4096), ("sector", 2048), ("head", 0), ("queueSize", 8)):
        _check(request.get(key) == expected and not isinstance(request.get(key), bool), "fixed request")
    if "backend" in request:
        _identifier(request["backend"], "backend")
    if "command" in request:
        _text(request["command"], "command")
    lengths, permissions, used_length = _layout(request)
    descriptors = request.get("descriptors")
    _check(isinstance(descriptors, list) and len(descriptors) == 3, "descriptors")
    ranges = []
    for index, descriptor in enumerate(descriptors):
        descriptor = _object(descriptor, "descriptor")
        _check(descriptor.get("id") == index and descriptor.get("len") == lengths[index]
               and descriptor.get("next") == (index + 1 if index < 2 else None), "descriptor layout")
        _check(descriptor.get("flags") == permissions[index], "descriptor permissions")
        address = descriptor.get("addr")
        _check(isinstance(address, str) and bool(re.fullmatch(r"0x[0-9a-f]+", address)), "descriptor address")
        start, end = int(address, 16), int(address, 16) + lengths[index]
        _integer(end, "descriptor address range")
        _check(not any(start < high and end > low for low, high in ranges), "overlapping descriptors")
        ranges.append((start, end))
        _text(descriptor.get("role"), "descriptor role")
    lanes = _indexed(scene.get("lanes"), "lanes")
    _check(5 <= len(lanes) <= 8 and set(lanes) <= _LANES
           and {"guest-user", "guest", "kvm", "memory"} <= set(lanes), "lane identities")
    for lane in lanes.values():
        _text(lane.get("title"), "lane title")
        _text(lane.get("subtitle"), "lane subtitle")
    phases = _indexed(scene.get("phases"), "phases")
    _check(len(phases) == 10, "ten phases required")
    for phase in phases.values():
        _text(phase.get("title"), "phase title")
        _text(phase.get("summary"), "phase summary")
    paths = _indexed(scene.get("callPaths"), "call paths")

    def references(item: dict) -> None:
        refs = item.get("sourceIds")
        _check(isinstance(refs, list) and bool(refs)
               and all(isinstance(key, str) and key in sources for key in refs)
               and len(set(refs)) == len(refs), "source references")

    for path in paths.values():
        _check(set(path) == {"id", "title", "context", "sourceIds", "boundary"}, "call path fields")
        for key in ("title", "context", "boundary"):
            _text(path.get(key), f"call path {key}")
        references(path)
    path_references = {reference for path in paths.values() for reference in path["sourceIds"]}
    state = deepcopy(_object(scene.get("initial"), "initial state"))
    _check(set(state) == _STATE_KEYS and all(state[key] is False for key in _FLAGS), "initial state fields")

    def validate_state(current: dict) -> None:
        _check(set(current) == _STATE_KEYS and all(type(current[key]) is bool for key in _FLAGS), "state fields")
        for key in _COUNTERS:
            _integer(current[key], f"state {key}")
        for flag, before in _DEPENDENCIES.items():
            _check(not current[flag] or current[before], f"{flag} requires {before}")
        for counter, flag in (("availIndex", "published"), ("backendIndex", "consumed"),
                              ("usedIndex", "usedPublished"), ("driverIndex", "reclaimed")):
            _check(current[counter] == int(current[flag]), f"state {counter}")
        _check(current["freeDescriptors"] == 8 - (3 if current["prepared"] and not current["reclaimed"] else 0),
               "descriptor ownership")
        _check(current["statusByte"] == ("0x00" if current["statusWritten"] else "0xff"), "completion marker")
        _check(current["usedId"] == (0 if current["usedWritten"] else None), "used identity")
        _check(current["usedLength"] == (used_length if current["usedWritten"] else None), "state used length")
        if request["operation"] == "read":
            _check(current["bufferReady"] == current["dataReady"], "READ buffer milestone")
        else:
            _check(not current["syscallEntered"] or current["bufferReady"], "WRITE payload before syscall")

    validate_state(state)
    steps = _indexed(scene.get("steps"), "steps")
    phase_ids, previous_phase, visited = list(phases), 0, set()
    for index, item in enumerate(steps.values()):
        for key in ("title", "description", "detail"):
            _text(item.get(key), f"step {key}")
        _check(isinstance(item.get("actor"), str) and item["actor"] in lanes, "step actor")
        _check(isinstance(item.get("phase"), str) and item["phase"] in phases, "step phase")
        phase = phase_ids.index(item["phase"])
        _check(previous_phase <= phase <= previous_phase + 1 and (index > 0 or phase == 0), "phase order")
        previous_phase = phase
        visited.add(item["phase"])
        references(item)
        _check(bool(path_references.intersection(item["sourceIds"])),
               f'step has no highlighted source path: {item["id"]}')
        active = item.get("active")
        _check(isinstance(active, list) and bool(active)
               and all(isinstance(key, str) and key in _NODES for key in active)
               and len(set(active)) == len(active), "active nodes")
        _check("edge" in item, "missing edge")
        if item["edge"] is not None:
            edge = _object(item["edge"], "edge")
            _check(set(edge) == {"from", "to", "kind", "label"}
                   and isinstance(edge["from"], str) and isinstance(edge["to"], str)
                   and edge["from"] in _NODES and edge["to"] in _NODES and edge["from"] != edge["to"]
                   and edge["kind"] in ("data", "metadata", "notification"), "edge fields")
            _text(edge["label"], "edge label")
        changes = _object(item.get("changes"), "state changes")
        _check(set(changes) <= _STATE_KEYS and (index > 0 or not changes), "state patch fields")
        updated = state | changes
        validate_state(updated)
        _check(all(not state[key] or updated[key] for key in _FLAGS), "non-monotonic state")
        for flag, before in _DEPENDENCIES.items():
            _check(state[flag] or not updated[flag] or state[before], f"{before} must precede {flag}")
        if request["operation"] == "write" and not state["syscallEntered"] and updated["syscallEntered"]:
            _check(state["bufferReady"], "payload must precede syscall")
        state = updated
    _check(visited == set(phases) and state["userReturned"], "incomplete playback")
    if "presentation" in scene:
        for key, value in _object(scene["presentation"], "presentation").items():
            _text(key, "presentation key")
            _text(value, "presentation value")
        mode = scene["presentation"].get("notificationMode", "eventfd")
        _check(mode in ("eventfd", "poll"), "notification mode")


def derive_base_operation(base: dict, operation: str = "read") -> dict:
    """Mirror the browser model's local READ/WRITE derivation without mutation."""
    _check(operation in ("read", "write"), "requested operation")
    _validate_scene(base)
    _check(base["request"]["operation"] == "read", "base must be READ")
    scene = deepcopy(base)
    if operation == "read":
        return scene
    variant = _object(_object(base.get("operationVariants"), "operation variants").get("write"), "WRITE variant")
    _check(set(variant) == {"title", "summary", "assumptions", "descriptors", "laneSubtitles",
                           "phasePatches", "sourceMap", "callPathPatches", "insertSteps", "stepPatches"},
           "WRITE variant fields")
    scene["id"] = "virtio-blk-write"
    for key in ("title", "summary", "assumptions"):
        scene[key] = deepcopy(variant[key])
    scene["request"]["operation"] = "write"
    if "command" in scene["request"]:
        scene["request"]["command"] = "OUT"
    if "usedLength" in scene["request"]:
        scene["request"]["usedLength"] = 1
    scene["request"]["descriptors"] = deepcopy(variant["descriptors"])
    lanes = _indexed(scene["lanes"], "lanes")
    for key, subtitle in _object(variant["laneSubtitles"], "lane subtitles").items():
        _check(key in lanes, "unknown lane subtitle target")
        lanes[key]["subtitle"] = subtitle
    _apply_delta(scene, {key: value for key, value in variant.items()
                         if key in {"phasePatches", "sourceMap", "callPathPatches", "insertSteps", "stepPatches"}})
    del scene["operationVariants"]
    _validate_scene(scene)
    return scene


def build_io_scenarios(base: dict, flow_dir: Path | str) -> dict:
    """Build all five backend routes and both operations from checked-in deltas.

    Unknown fields, targets, conflicting evidence and incomplete traces fail closed;
    rendering never silently substitutes the local-file trace for another backend.
    """
    directory = Path(flow_dir)
    local = {operation: derive_base_operation(base, operation) for operation in ("read", "write")}
    catalog = {"local-blk": {"id": "local-blk", "label": "virtio-blk · QEMU 本地文件", "scenarios": local}}
    for filename in _FILES:
        payload = _object(json.loads((directory / filename).read_text(encoding="utf-8")), filename)
        _check({"schemaVersion", "routes"} <= set(payload)
               and set(payload) <= {"schemaVersion", "routes", "id", "reviewedOn"}
               and type(payload["schemaVersion"]) is int and payload["schemaVersion"] == 1,
               "variant file schema")
        if "id" in payload:
            _identifier(payload["id"], "variant file id")
        if "reviewedOn" in payload:
            _check(isinstance(payload["reviewedOn"], str)
                   and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["reviewedOn"])),
                   "variant review date")
        routes = _indexed(payload["routes"], "variant routes")
        for backend, route in routes.items():
            _check(backend in _ROUTES and backend not in catalog, "unknown or duplicate backend")
            _check(set(route) <= {"id", "label", "baselines", "sources", "common", "operations"}
                   and {"id", "label", "common", "operations"} <= set(route), "route fields")
            _text(route["label"], "route label")
            operations = _object(route["operations"], "route operations")
            _check(set(operations) == {"read", "write"}, "route requires READ and WRITE")
            scenes = {}
            for operation in ("read", "write"):
                scene = deepcopy(local[operation])
                scene.pop("operationVariants", None)
                _merge_evidence(scene, route)
                _apply_delta(scene, route["common"])
                _apply_delta(scene, operations[operation])
                scene["id"] = f"{backend}-{operation}"
                _check(scene["request"].get("backend") == backend
                       and scene["request"].get("operation") == operation
                       and "interface" in scene["request"], "route request identity")
                _validate_scene(scene)
                scenes[operation] = scene
            catalog[backend] = {"id": backend, "label": route["label"], "scenarios": scenes}
    _check(set(catalog) == set(_ROUTES), "missing backend routes")
    return {"routes": [catalog[key] for key in _ROUTES]}
