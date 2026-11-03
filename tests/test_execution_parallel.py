"""Parallel branches preserve hard prerequisites without inventing global order."""
from copy import deepcopy

import pytest

from virt_report.execution_demos import get_demo, validate_demo
from virt_report.render.render import render_mechanisms_html
from virt_report.config import Config


def test_parallel_phases_never_cross_branch_boundaries():
    demo = get_demo("network-io", presentation=False)
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
    demo = deepcopy(get_demo("network-io", presentation=False))
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


def test_no_script_fallback_uses_one_success_path_per_backend_and_direction():
    page = render_mechanisms_html(Config(), "network-io")
    assert "TAP 接受后，队列归还与宿主转发分别推进" not in page
    assert "qemu-rx-no-buffer" not in page
    assert "qemu-tx-backpressure" not in page
    for scene in get_demo("network-io")["scenarios"]:
        assert "parallel" not in scene
