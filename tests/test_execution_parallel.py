"""Parallel branches preserve hard prerequisites without inventing global order."""
from copy import deepcopy

import pytest

from virt_report.execution_demos import get_demo, validate_demo
from virt_report.render.render import render_mechanisms_html
from virt_report.config import Config


def test_parallel_phases_never_cross_branch_boundaries():
    demo = get_demo("network-io")
    validate_demo(demo)
    scenes = [scene for scene in demo["scenarios"] if "parallel" in scene]
    assert {scene["id"] for scene in scenes} == {
        "qemu-tx", "vhost-tx", "qemu-tx-backpressure", "dpdk-tx", "dpdk-rx",
    }
    for scene in scenes:
        branches = scene["parallel"]["branches"]
        starts = {phase["start"] for phase in scene["phases"]}
        assert all(branch["steps"][0] in starts for branch in branches)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "prefix", "unordered", "unknown", "phase"])
def test_parallel_metadata_rejects_invalid_dependencies(mutation):
    demo = deepcopy(get_demo("network-io"))
    scene = demo["scenarios"][0]
    parallel = scene["parallel"]
    first, second = parallel["branches"]
    if mutation == "missing":
        first["steps"].pop()
    elif mutation == "duplicate":
        second["steps"].append(first["steps"][-1])
    elif mutation == "prefix":
        first["steps"].insert(0, "initial")
    elif mutation == "unordered":
        first["steps"].reverse()
    elif mutation == "unknown":
        parallel["after"] = "unknown"
    else:
        scene["phases"] = [phase for phase in scene["phases"] if phase["start"] != first["steps"][0]]
    with pytest.raises(ValueError, match="parallel"):
        validate_demo(demo)


def test_no_script_fallback_also_separates_parallel_paths():
    page = render_mechanisms_html(Config(), "network-io")
    assert "并行分支分别列出，不规定分支之间的先后" in page
    for scene in get_demo("network-io")["scenarios"]:
        if "parallel" in scene:
            for branch in scene["parallel"]["branches"]:
                assert f'<h3>{branch["title"]}</h3>' in page
