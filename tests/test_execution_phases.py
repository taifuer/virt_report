"""All execution scenarios expose a continuous, reviewed stage outline."""
import copy

import pytest

from virt_report.execution_demos import DEMO_IDS, get_demo, validate_demo


@pytest.mark.parametrize("demo_id", DEMO_IDS)
def test_phases_cover_scenario_once_in_causal_order(demo_id):
    demo = get_demo(demo_id)
    for scene in demo["scenarios"]:
        phases = scene["phases"]
        assert 2 <= len(phases) <= 10
        positions = {step["id"]: n for n, step in enumerate(scene["steps"])}
        starts = [positions[phase["start"]] for phase in phases]
        assert starts[0] == 0
        assert starts == sorted(set(starts))
        assert len({phase["id"] for phase in phases}) == len(phases)
        assert all(phase["title"].strip() for phase in phases)
        if len(scene["steps"]) > 10:
            assert len(phases) < len(scene["steps"]) - 2


@pytest.mark.parametrize("broken", ["unknown", "repeated", "unordered", "initial", "title"])
def test_phases_reject_invalid_boundaries(broken):
    demo = copy.deepcopy(get_demo("network-io"))
    phases = demo["scenarios"][0]["phases"]
    if broken == "unknown":
        phases[-1]["start"] = "unknown"
    elif broken == "repeated":
        phases[-1]["start"] = phases[0]["start"]
    elif broken == "unordered":
        phases[1], phases[2] = phases[2], phases[1]
    elif broken == "initial":
        phases.pop(0)
    else:
        phases[-1]["title"] = " "
    with pytest.raises(ValueError, match="phase"):
        validate_demo(demo)
