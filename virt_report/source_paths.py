"""Validate editorial relationships without inferring calls from source order."""
from __future__ import annotations

import re


FLOW_KINDS = {"sequence", "call", "via", "return"}
ENTRY_KINDS = {"notification", "async", "callback", "return"}


def is_implementation_source(source: dict) -> bool:
    """Match the browser's implementation/reference split."""
    if source.get("kind") in {"spec", "specification", "reference", "hardware"}:
        return False
    return source.get("kind") in {"function", "assembly"} or bool(
        source.get("symbol") and re.search(r"\.(?:c|h|S|s|cc|cpp)$", source.get("path", ""))
    )


def validate_call_path_metadata(path: dict, sources: dict) -> None:
    """Reject ambiguous or stale optional edge metadata before static export."""
    flow = path.get("flow", "sequence")
    if not isinstance(flow, str) or flow not in FLOW_KINDS:
        raise ValueError("Invalid source path flow")
    if "entry" in path:
        entry = path["entry"]
        if (not isinstance(entry, dict) or set(entry) != {"kind", "label"}
                or not isinstance(entry["kind"], str) or entry["kind"] not in ENTRY_KINDS
                or not isinstance(entry["label"], str) or not entry["label"].strip()):
            raise ValueError("Invalid source path entry")
    relations = path.get("relations", [])
    if not isinstance(relations, list):
        raise ValueError("Invalid source path relations")
    implementation = [key for key in path["sourceIds"] if is_implementation_source(sources[key])]
    adjacent = set(zip(implementation, implementation[1:]))
    seen = set()
    for relation in relations:
        if (not isinstance(relation, dict) or set(relation) != {"from", "to", "kind"}
                or not all(isinstance(value, str) for value in relation.values())
                or relation["kind"] not in FLOW_KINDS):
            raise ValueError("Invalid source path relation")
        pair = relation["from"], relation["to"]
        if pair not in adjacent or pair in seen:
            raise ValueError("Source path relation must join unique adjacent implementations")
        seen.add(pair)
