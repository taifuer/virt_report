"""Pure lifecycle-model regressions for the IOMMUFD article."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "virt_report/render/assets/iommufd-lab.js"


@pytest.fixture(scope="module")
def runs():
    if shutil.which("node") is None:
        pytest.skip("Node is optional for browser-model tests")
    result = subprocess.run(
        [
            "node", "-e",
            "const m=require(process.argv[1]); "
            "console.log(JSON.stringify(Object.fromEntries("
            "m.MODES.map(mode=>[mode,m.buildRun(mode)]))))",
            str(SCRIPT),
        ],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_dma_requires_all_dependencies_and_stops_before_cleanup(runs):
    for states in runs.values():
        for state in states:
            if state["hwpt"]:
                assert state["ioas"]
            if state["attached"]:
                assert state["bound"] and state["hwpt"]
            if state["mapped"]:
                assert state["ioas"]
            if state["dmaRunning"]:
                assert state["bound"] and state["attached"] and state["mapped"]
                assert not state["fault"]
            if state["key"].startswith(("destroy-", "close-")):
                assert not state["dmaRunning"]
    states = runs["success"]
    keys = [state["key"] for state in states]
    assert keys.index("attach") < keys.index("map") < keys.index("dma")
    assert keys.index("stop") < keys.index("detach") < keys.index("destroy-hwpt")
    assert keys.index("unmap") < keys.index("destroy-ioas")


def test_detach_preserves_mapping_and_binding(runs):
    state = next(state for state in runs["success"] if state["key"] == "detach")
    assert not state["attached"]
    assert state["bound"] and state["hwpt"] and state["ioas"] and state["mapped"]


def test_unmapped_state_persists_through_remaining_cleanup(runs):
    states = runs["success"]
    unmap_index = next(i for i, state in enumerate(states) if state["key"] == "unmap")
    assert not any(state["unmapped"] for state in states[:unmap_index])
    assert all(state["unmapped"] and not state["mapped"] for state in states[unmap_index:])


def test_failure_rollbacks_never_start_dma_or_unmap_missing_memory(runs):
    for mode in ("attach-failure", "map-failure"):
        states = runs[mode]
        assert any(state["fault"] for state in states)
        assert not any(state["dmaRequests"] or state["dmaRunning"] for state in states)
        assert not any(state["mapped"] for state in states)
        assert "unmap" not in [state["key"] for state in states]
    assert not any(state["attached"] for state in runs["attach-failure"])
    assert any(state["attached"] for state in runs["map-failure"])


def test_every_path_releases_only_after_dependencies_are_gone(runs):
    for states in runs.values():
        last = states[-1]
        assert last["released"]
        for key in ("deviceFd", "contextFd", "bound", "ioas", "hwpt", "attached", "mapped", "dmaRunning"):
            assert not last[key]
        destroy_ioas = next(state for state in states if state["key"] == "destroy-ioas")
        assert destroy_ioas["bound"]
        assert not destroy_ioas["hwpt"] and not destroy_ioas["mapped"]
        close_device = next(state for state in states if state["key"] == "close-device")
        assert not close_device["bound"] and close_device["contextFd"]


def test_back_navigation_and_invalid_inputs():
    if shutil.which("node") is None:
        pytest.skip("Node is optional for browser-model tests")
    subprocess.run(
        ["node", "-e", """
const assert = require('node:assert/strict');
const m = require(process.argv[1]);
for (const mode of m.MODES) {
  let s = m.frame(mode, 0);
  const history = [s];
  while (!s.released) {
    const before = JSON.stringify(s);
    const next = m.step(s);
    assert.equal(JSON.stringify(s), before);
    history.push(next); s = next;
  }
  assert.deepEqual(m.step(s), s);
  for (let i = history.length - 2; i >= 0; i--) {
    s = m.back(s); assert.deepEqual(s, history[i]);
  }
  assert.deepEqual(m.back(s), s);
}
assert.throws(() => m.buildRun('invalid'), RangeError);
for (const index of [-1, 1.5, NaN, 100]) {
  assert.throws(() => m.frame('success', index), RangeError);
}
""", str(SCRIPT)],
        check=True, capture_output=True, text=True,
    )
