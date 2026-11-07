"""Validated, offline teaching traces for execution and I/O walkthroughs."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from virt_report.source_paths import validate_call_path_metadata

DEMO_PAGES = {
    "network-io": "network-io",
    "memory-access": "memory-access",
    "cpu-execution": "cpu-execution",
    "interrupt-delivery": "interrupt-delivery",
    "disk-persistence": "virtio-blk-read",
    "vm-lifecycle": "libvirt-domain-start",
    "gpu-execution": "gpu-execution",
    "virtio-gpu-rendering": "virtio-gpu-execution",
    "virtio-balloon": "virtio-balloon",
}
DEMO_IDS = tuple(DEMO_PAGES)
CONTENT_DIR = Path(__file__).parent / "content" / "flows"


def validate_demo(demo: dict) -> None:
    """Reject broken references before a page is published."""
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(f"Invalid execution demo: {message}")

    def text(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def ids(items: list[dict]) -> set[str]:
        values = [item.get("id") for item in items]
        require(all(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]*", value)
                    for value in values), "invalid identifier")
        require(len(set(values)) == len(values), "duplicate identifier")
        return set(values)

    require(demo.get("id") in DEMO_IDS, "unregistered demo")
    require(text(demo.get("title")), "missing title")
    sources = demo["sources"]
    require(bool(sources), "missing sources")
    for source in sources.values():
        url = urlsplit(source["url"])
        require(url.scheme == "https" and bool(url.netloc) and text(source.get("label")),
                "invalid source")
        if url.netloc == "github.com" and "/blob/" in url.path:
            require(bool(re.search(r"/blob/[a-f0-9]{40}/", url.path)), "unpinned source")
    require(bool(demo["scenarios"]), "missing scenarios")
    scenario_ids = ids(demo["scenarios"])
    require(demo["defaultScenario"] in scenario_ids, "unknown default scenario")
    for scene in demo["scenarios"]:
        require(text(scene["label"]) and text(scene["summary"]), "scenario labels")
        require(bool(scene["assumptions"]) and all(map(text, scene["assumptions"])), "assumptions")
        nodes = ids(scene["nodes"])
        require(1 < len(nodes) <= 48, "node count")
        require(all(text(node["label"]) and text(node["role"]) for node in scene["nodes"]), "nodes")
        require(all("initial" not in node or text(node["initial"]) for node in scene["nodes"]),
                "node initial state")
        require(all("badge" not in node or node["badge"] in {"用户态", "内核态"}
                    for node in scene["nodes"]), "node execution mode badge")
        links = ids(scene["links"])
        for link in scene["links"]:
            require(link["from"] in nodes and link["to"] in nodes, "link endpoints")
            require(link["kind"] in {"data", "control", "translation", "notification"}, "link kind")
            require(text(link["label"]), "link label")
        ids(scene["structures"])
        fields = []
        for structure in scene["structures"]:
            require(structure["kind"] in {"queue", "address", "registers", "interrupt"}, "structure kind")
            require(text(structure["title"]) and bool(structure["fields"]), "structure")
            for field in structure["fields"]:
                require(all(text(field.get(key)) for key in ("key", "label", "initial")), "field")
                require("hidden" not in field or isinstance(field["hidden"], bool),
                        "field visibility")
                require(field["key"] not in {"__proto__", "constructor", "prototype"}, "unsafe field")
                fields.append(field["key"])
        require(len(fields) == len(set(fields)), "duplicate fields")
        if "completion" in scene:
            completion = scene["completion"]
            require(isinstance(completion, list) and 1 <= len(completion) <= 4,
                    "completion boundary count")
            keys = [item.get("key") for item in completion]
            require(len(set(keys)) == len(keys) and set(keys) <= set(fields),
                    "completion boundary fields")
            require(all(text(item.get("label")) for item in completion), "completion labels")
        require(2 <= len(scene["steps"]) <= 120, "step count")
        ids(scene["steps"])
        if "parallel" in scene:
            parallel = scene["parallel"]
            require(all(text(parallel.get(key)) for key in ("title", "description")), "parallel labels")
            positions = {step["id"]: n for n, step in enumerate(scene["steps"])}
            require(parallel.get("after") in positions, "parallel fork")
            branches = parallel["branches"]
            require(len(branches) == 2, "parallel branch count")
            ids(branches)
            represented = []
            for branch in branches:
                require(all(text(branch.get(key)) for key in ("title", "summary")), "parallel branch labels")
                steps = branch["steps"]
                require(bool(steps) and set(steps) <= positions.keys(), "parallel branch steps")
                indexes = [positions[step] for step in steps]
                require(indexes == sorted(set(indexes)), "parallel branch order")
                represented.extend(steps)
            suffix = {step["id"] for step in scene["steps"][positions[parallel["after"]] + 1:]}
            require(set(represented) == suffix and len(represented) == len(suffix), "parallel coverage")
        if "phases" in scene:
            phases = scene["phases"]
            require(2 <= len(phases) <= 10, "phase count")
            ids(phases)
            require(all(text(phase.get("title")) for phase in phases), "phase titles")
            step_positions = {step["id"]: n for n, step in enumerate(scene["steps"])}
            require(all(phase.get("start") in step_positions for phase in phases), "phase start")
            starts = [step_positions[phase["start"]] for phase in phases]
            require(starts[0] == 0 and starts == sorted(set(starts)), "phase order and coverage")
            if "parallel" in scene:
                membership = {step: branch["id"] for branch in scene["parallel"]["branches"] for step in branch["steps"]}
                for start, end in zip(starts, [*starts[1:], len(scene["steps"])]):
                    owners = {membership.get(step["id"]) for step in scene["steps"][start:end]}
                    require(len(owners) == 1, "phase crosses parallel boundary")
        initial = scene["steps"][0]
        require(not initial["active"] and not initial["changes"] and not initial["links"]
                and initial["focus"], "initial state must be neutral")
        for step in scene["steps"]:
            require(all(text(step.get(key)) for key in ("title", "actor", "description", "detail")), "step text")
            require(set(step["active"]) <= nodes and set(step["links"]) <= links, "step references")
            require(set(step["changes"]) <= set(fields), "unknown changed field")
            require(set(step["nodeStates"]) <= nodes, "unknown node status")
            require(all(map(text, step["changes"].values()))
                    and all(map(text, step["nodeStates"].values())), "state values")
            require(bool(step["sourceIds"]) and set(step["sourceIds"]) <= set(sources), "step sources")
            require(isinstance(step["focus"], bool), "focus flag")
        if "callPaths" in scene:
            ids(scene["callPaths"])
            for path in scene["callPaths"]:
                require(all(text(path.get(key)) for key in ("title", "context", "boundary")),
                        "call path context")
                require(bool(path["sourceIds"]) and set(path["sourceIds"]) <= set(sources),
                        "call path sources")
                validate_call_path_metadata(path, sources)
        if "sourceSteps" in scene:
            ids(scene["sourceSteps"])
            for step in scene["sourceSteps"]:
                require(all(text(step.get(key)) for key in ("title", "context", "detail")),
                        "source step text")
                require(step.get("kind") in {"instruction", "function", "assembly"},
                        "source step kind")
                require(bool(step["stepIds"]) and set(step["stepIds"]) <= ids(scene["steps"]),
                        "source step mapping")
                require(bool(step["sourceIds"]) and set(step["sourceIds"]) <= set(sources),
                        "source step evidence")


def validate_selector(selector: dict, scenario_ids: set[str]) -> None:
    """Validate curated presets; category/direction controls never invent combinations."""
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(f"Invalid execution selector: {message}")

    def text(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def identifier(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]*", value))

    require(set(selector) <= {"categoryLabel", "scenarioLabel", "categories", "groups", "operations"},
            "unknown fields")
    require(text(selector.get("scenarioLabel")), "missing scenario label")
    require(("categories" in selector) != ("groups" in selector), "choose categories or groups")
    operations = selector.get("operations", [])
    operation_ids = [item.get("id") for item in operations]
    require(all(identifier(value) for value in operation_ids)
            and len(operation_ids) == len(set(operation_ids)), "invalid operations")
    require(all(set(item) == {"id", "label"} and text(item["label"]) for item in operations),
            "operation labels")
    if "categories" in selector:
        categories = selector["categories"]
        require(bool(categories) and text(selector.get("categoryLabel")), "missing categories")
        category_ids = [item.get("id") for item in categories]
        require(all(identifier(value) for value in category_ids)
                and len(category_ids) == len(set(category_ids)), "invalid categories")
        require(all(set(item) == {"id", "label", "groups"} and text(item["label"])
                    for item in categories), "category labels")
    else:
        require(not operations and "categoryLabel" not in selector, "operations require categories")
        categories = [{"groups": selector["groups"]}]
    represented = []
    for category in categories:
        groups = category["groups"]
        require(bool(groups), "empty category")
        if operations:
            represented_operations = [group.get("operation") for group in groups]
            require(all(identifier(value) for value in represented_operations)
                    and sorted(represented_operations) == sorted(operation_ids), "operation coverage")
        for group in groups:
            fields = {"label", "scenarios", "operation"} if operations else {"label", "scenarios"}
            require(set(group) - {"advanced"} == fields, "group fields")
            require("advanced" not in group or isinstance(group["advanced"], bool),
                    "advanced flag")
            require(not group.get("advanced") or not operations,
                    "advanced presets cannot combine with operation controls")
            require(text(group["label"]) and bool(group["scenarios"]), "empty group")
            for scene in group["scenarios"]:
                require(set(scene) == {"id", "label"} and identifier(scene["id"])
                        and text(scene["label"]), "scenario option")
                represented.append(scene["id"])
        require(any(not group.get("advanced") for group in groups), "missing primary scenarios")
    require(len(represented) == len(set(represented)), "duplicate scenario")
    require(set(represented) == scenario_ids, "scenario coverage")


def load_demo(demo_id: str) -> dict:
    """Load fresh editorial input before enrichment and presentation filtering."""
    if demo_id not in DEMO_IDS:
        raise ValueError("Unregistered demo")
    if demo_id == "virtio-balloon":
        from virt_report.balloon_demo import build_demo
        return build_demo()
    return json.loads((CONTENT_DIR / f"{demo_id}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=2 * len(DEMO_IDS))
def get_demo(demo_id: str, *, presentation: bool = True) -> dict | None:
    """Load offline traces; reference mode retains alternatives for validation."""
    if demo_id not in DEMO_IDS:
        return None
    demo = load_demo(demo_id)
    if demo_id == "network-io":
        from virt_report.network_queue import enrich_network_demo
        enrich_network_demo(demo)
    phases = json.loads((CONTENT_DIR / "execution-phases.json").read_text(encoding="utf-8"))
    scene_phases = phases.get(demo_id, {scene["id"]: scene.get("phases", [])
                                       for scene in demo["scenarios"]})
    if set(scene_phases) != {scene["id"] for scene in demo["scenarios"]}:
        raise ValueError("Execution phases must cover each scenario exactly once")
    for scene in demo["scenarios"]:
        scene["phases"] = scene_phases[scene["id"]]
    validate_demo(demo)
    selectors = json.loads((CONTENT_DIR / "execution-selectors.json").read_text(encoding="utf-8"))
    selector = demo.get("selector", selectors.get(demo_id))
    validate_selector(selector, {scene["id"] for scene in demo["scenarios"]})
    demo["selector"] = selector
    layouts = json.loads((CONTENT_DIR / "execution-layouts.json").read_text(encoding="utf-8"))
    for scene in demo["scenarios"]:
        layout = scene.get("layout") or layouts[demo_id][scene["id"]]
        validate_execution_layout(layout, {node["id"] for node in scene["nodes"]})
        scene["layout"] = layout
    if demo_id == "network-io" and presentation:
        from virt_report.network_queue import simplify_network_presentation
        simplify_network_presentation(demo)
        validate_demo(demo)
        validate_selector(demo["selector"], {scene["id"] for scene in demo["scenarios"]})
    if demo["id"] != demo_id:
        raise ValueError("Execution demo identity mismatch")
    return demo


def validate_execution_layout(layout: dict, node_ids: set[str]) -> None:
    """Separate real execution nodes from non-animated background responsibilities."""
    components = {"guest", "kvm", "qemu", "host-kernel", "hardware",
                  "host-user", "external", "backend"}
    domains = layout.get("domains", [])
    if not domains or len({group["id"] for group in domains}) != len(domains):
        raise ValueError("Execution layout needs uniquely identified domains")
    for group in domains:
        if group.get("component") not in components:
            raise ValueError("Execution layout has an unknown component")
        if not isinstance(group.get("nodes"), list):
            raise ValueError("Execution domain nodes must be a list")
        if not group["nodes"] and not group.get("note", "").strip():
            raise ValueError("Background execution domains require an explanatory note")
        for field in ("title", "context", "note"):
            if field in group and (not isinstance(group[field], str) or not group[field].strip()):
                raise ValueError(f"Execution domain {field} must be nonempty text")
    groups = [*domains]
    if layout.get("memory"):
        groups.append(layout["memory"])
    represented = [node for group in groups for node in group["nodes"]]
    if len(represented) != len(node_ids) or set(represented) != node_ids:
        raise ValueError("Execution layout must include each scene node exactly once")
