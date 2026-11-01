"""Validated, offline teaching traces for execution and I/O walkthroughs."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

DEMO_PAGES = {
    "network-io": "network-io",
    "memory-access": "memory-access",
    "cpu-execution": "cpu-execution",
    "interrupt-delivery": "interrupt-delivery",
    "disk-persistence": "virtio-blk-read",
    "vm-lifecycle": "libvirt-domain-start",
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
        require(1 < len(nodes) <= 8, "node count")
        require(all(text(node["label"]) and text(node["role"]) for node in scene["nodes"]), "nodes")
        require(all("initial" not in node or text(node["initial"]) for node in scene["nodes"]),
                "node initial state")
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
                require(field["key"] not in {"__proto__", "constructor", "prototype"}, "unsafe field")
                fields.append(field["key"])
        require(len(fields) == len(set(fields)), "duplicate fields")
        require(2 <= len(scene["steps"]) <= 20, "step count")
        ids(scene["steps"])
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
            require(set(group) == ({"label", "scenarios", "operation"} if operations
                                   else {"label", "scenarios"}), "group fields")
            require(text(group["label"]) and bool(group["scenarios"]), "empty group")
            for scene in group["scenarios"]:
                require(set(scene) == {"id", "label"} and identifier(scene["id"])
                        and text(scene["label"]), "scenario option")
                represented.append(scene["id"])
    require(len(represented) == len(set(represented)), "duplicate scenario")
    require(set(represented) == scenario_ids, "scenario coverage")


@lru_cache(maxsize=len(DEMO_IDS))
def get_demo(demo_id: str) -> dict | None:
    """Load an allowlisted trace without accessing live systems or services."""
    if demo_id not in DEMO_IDS:
        return None
    demo = json.loads((CONTENT_DIR / f"{demo_id}.json").read_text(encoding="utf-8"))
    validate_demo(demo)
    selectors = json.loads((CONTENT_DIR / "execution-selectors.json").read_text(encoding="utf-8"))
    selector = selectors[demo_id]
    validate_selector(selector, {scene["id"] for scene in demo["scenarios"]})
    demo["selector"] = selector
    layouts = json.loads((CONTENT_DIR / "execution-layouts.json").read_text(encoding="utf-8"))
    for scene in demo["scenarios"]:
        layout = layouts[demo_id][scene["id"]]
        groups = [*layout["domains"]]
        if layout.get("memory"):
            groups.append(layout["memory"])
        represented = [node for group in groups for node in group["nodes"]]
        expected = {node["id"] for node in scene["nodes"]}
        if len(represented) != len(expected) or set(represented) != expected:
            raise ValueError("Execution layout must include each scene node exactly once")
        scene["layout"] = layout
    if demo["id"] != demo_id:
        raise ValueError("Execution demo identity mismatch")
    return demo
