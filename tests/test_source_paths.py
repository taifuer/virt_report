"""Optional source edges must be explicit and tied to displayed implementations."""
from copy import deepcopy

import pytest

from virt_report.source_paths import is_implementation_source, validate_call_path_metadata


SOURCES = {
    "first": {"symbol": "first", "path": "first.c"},
    "spec": {"kind": "spec", "label": "Specification"},
    "second": {"kind": "assembly", "symbol": "entry"},
    "third": {"kind": "function", "symbol": "third"},
}
PATH = {"sourceIds": list(SOURCES)}


def test_default_paths_do_not_claim_direct_calls():
    original = deepcopy(PATH)
    validate_call_path_metadata(PATH, SOURCES)
    assert PATH == original


def test_adjacent_relations_ignore_specification_nodes():
    validate_call_path_metadata({**PATH, "flow": "return", "relations": [
        {"from": "first", "to": "second", "kind": "via"},
    ], "entry": {"kind": "callback", "label": "完成后调度回调"}}, SOURCES)


@pytest.mark.parametrize("metadata", [
    {"flow": "async"}, {"flow": []}, {"entry": {}}, {"entry": None},
    {"entry": {"kind": "call", "label": "调用"}},
    {"entry": {"kind": "callback", "label": " "}},
    {"relations": {}}, {"relations": [None]},
    {"relations": [{"from": "first", "to": "third", "kind": "call"}]},
    {"relations": [{"from": "first", "to": "spec", "kind": "call"}]},
    {"relations": [{"from": "second", "to": "first", "kind": "return"}]},
    {"relations": [{"from": "first", "to": "second", "kind": "async"}]},
    {"relations": [{"from": "first", "to": "second", "kind": "call"}] * 2},
])
def test_rejects_invalid_or_stale_relation_metadata(metadata):
    with pytest.raises(ValueError, match="[Ss]ource path"):
        validate_call_path_metadata({**PATH, **metadata}, SOURCES)


@pytest.mark.parametrize(("source", "expected"), [
    ({"kind": "hardware", "symbol": "SDM", "path": "cpu.c"}, False),
    ({"kind": "function", "symbol": "submit"}, True),
    ({"symbol": "entry", "path": "entry_64.S"}, True),
    ({"symbol": "manual", "path": "manual.rst"}, False),
    ({"label": "API", "path": "api.h"}, False),
])
def test_implementation_detection(source, expected):
    assert is_implementation_source(source) is expected
