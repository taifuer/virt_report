"""State and consistency barriers in the VFIO migration article's example."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "virt_report/render/assets/vfio-migration-lab.js"


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


def test_only_one_endpoint_can_run_and_dma_requires_running_device(runs):
    for states in runs.values():
        for state in states:
            assert not (state["sourceCpu"] and state["targetCpu"])
            assert not (state["sourceDma"] and state["targetDma"])
            if state["sourceDma"]:
                assert state["sourceDevice"] in ("RUNNING", "PRE_COPY")
            if state["targetDma"]:
                assert state["targetDevice"] == "RUNNING"
                assert state["authority"] == "target"
            if state["targetCpu"]:
                assert state["validated"] and state["ramFinal"] and state["deviceFinal"]
                assert state["sourceRam"] == state["targetRam"]
                assert state["stopped"]


def test_pausing_cpu_does_not_by_itself_stop_device_dma(runs):
    for mode in ("precopy", "stopcopy"):
        states = runs[mode]
        pause = next(state for state in states if state["key"] == "pause-cpu")
        assert not pause["sourceCpu"] and pause["sourceDma"]
        assert pause["sourceRam"][2] == 2 and pause["targetRam"][2] == 1
        final = next(state for state in states if state["key"] == "final-ram")
        assert not final["sourceCpu"] and not final["sourceDma"]
        assert final["sourceRam"] == final["targetRam"]


def test_initial_ack_neither_means_complete_state_nor_handoff(runs):
    state = next(state for state in runs["precopy"] if state["key"] == "initial-ack")
    assert state["initialLoaded"] and state["ack"]
    assert state["sourceCpu"] and state["sourceDma"]
    assert state["targetDevice"] == "RESUMING"
    assert not state["deviceFinal"] and not state["ramFinal"]
    assert not state["targetCpu"] and not state["targetDma"]
    assert state["authority"] == "source"


def test_stopcopy_device_still_allows_running_ram_precopy(runs):
    states = runs["stopcopy"]
    assert not any(state["ack"] or state["initialLoaded"] for state in states)
    assert not any(state["sourceDevice"] == "PRE_COPY" for state in states)
    ram = next(state for state in states if state["key"] == "ram-iterate")
    assert ram["sourceCpu"] and ram["sourceDevice"] == "RUNNING"
    begin = next(state for state in states if state["key"] == "begin-stopcopy")
    assert begin["sourceDevice"] == "STOP_COPY" and not begin["sourceDma"]


def test_device_tail_needs_validation_before_destination_runs(runs):
    for mode in ("precopy", "stopcopy"):
        states = runs[mode]
        tail = next(state for state in states if state["key"] == "final-device")
        assert tail["deviceFinal"] and not tail["validated"]
        assert tail["targetDevice"] == "RESUMING" and not tail["targetCpu"]
        last = states[-1]
        assert last["done"] and last["authority"] == "target"
        assert last["sourceDevice"] == "STOP" and not last["sourceCpu"]


def test_cancel_is_confined_to_running_source_and_resets_incoming_session(runs):
    states = runs["cancel"]
    assert all(state["sourceCpu"] and state["sourceDma"] for state in states)
    assert not any(state["targetCpu"] or state["targetDma"] for state in states)
    assert not any(state["ramFinal"] or state["deviceFinal"] for state in states)
    reset_index = next(i for i, state in enumerate(states) if state["key"] == "cancel-reset")
    reset = states[reset_index]
    assert reset["targetDevice"] == "RUNNING"
    assert not reset["targetCpu"] and not reset["targetDma"]
    assert "VFIO_DEVICE_RESET" in reset["action"]
    stopped = states[reset_index + 1]
    assert stopped["key"] == "cancel-target" and stopped["targetDevice"] == "STOP"
    assert "SET_STATE(STOP)" in stopped["action"]
    last = states[-1]
    assert last["sourceDevice"] == "RUNNING" and last["targetDevice"] == "STOP"
    assert last["targetRam"] == [0, 0, 0, 0]
    assert last["cancelled"] and last["done"] and last["authority"] == "source"


def test_frames_are_independent_and_navigation_is_reversible():
    if shutil.which("node") is None:
        pytest.skip("Node is optional for browser-model tests")
    subprocess.run(
        ["node", "-e", """
const assert = require('node:assert/strict');
const m = require(process.argv[1]);
for (const mode of m.MODES) {
  let s = m.frame(mode, 0);
  const history = [s];
  while (!s.done) {
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
  const frames = m.buildRun(mode);
  frames[0].sourceRam[0] = 900;
  assert.equal(frames[1].sourceRam[0], 1);
}
assert.throws(() => m.buildRun('invalid'), RangeError);
for (const index of [-1, 1.5, NaN, 100]) {
  assert.throws(() => m.frame('precopy', index), RangeError);
}
""", str(SCRIPT)],
        check=True, capture_output=True, text=True,
    )
