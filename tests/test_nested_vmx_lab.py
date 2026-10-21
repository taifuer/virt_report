"""Nested VMX teaching model preserves execution and exit ownership boundaries."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "virt_report/render/assets/nested-vmx-lab.js"


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


def test_every_scenario_uses_hardware_vmcs_for_the_running_guest(runs):
    for states in runs.values():
        for state in states:
            assert state["currentVmcs"] in ("01", "02")
            if state["active"] == "l1":
                assert state["currentVmcs"] == "01"
            if state["active"] == "l2":
                assert state["currentVmcs"] == "02"
                assert state["prepared02"]
            if not state["prepared02"]:
                assert state["rip02"] is None


def test_all_io_first_exits_to_l0_with_uncompleted_instruction(runs):
    for states in runs.values():
        io_exit = next(s for s in states if s["key"] == "io-exit")
        assert io_exit["active"] == "l0"
        assert io_exit["currentVmcs"] == "02"
        assert io_exit["ioExited"] and not io_exit["completed"]
        assert io_exit["rip02"] == io_exit["rip12"] == 0x1000
        assert io_exit["reflections"] == io_exit["signals"] == io_exit["userExits"] == 0


def test_l1_reflection_saves_state_then_runs_l1_then_recomposes(runs):
    states = runs["l1-intercept"]
    keys = [s["key"] for s in states]
    assert keys.index("reflect") < keys.index("l1-handle") < keys.index("l1-complete")
    assert keys.index("l1-complete") < keys.index("resume") < keys.index("recompose")
    reflect = next(s for s in states if s["key"] == "reflect")
    assert reflect["active"] == "l0" and reflect["currentVmcs"] == "01"
    assert reflect["exit12"] and reflect["reflections"] == 1
    assert reflect["rip12"] == 0x1000 and not reflect["completed"]
    handled = next(s for s in states if s["key"] == "l1-complete")
    assert handled["rip12"] == 0x1002 and handled["rip02"] == 0x1000
    assert handled["completed"]
    assert states[-1]["rip02"] == states[-1]["rip12"] == 0x1002
    assert not any(s["signals"] or s["userExits"] for s in states)


def test_kernel_backend_completes_without_l1_or_outer_userspace(runs):
    states = runs["l0-eventfd"]
    assert all(not s["exit12"] and not s["reflections"] and not s["userExits"] for s in states)
    complete = next(s for s in states if s["key"] == "l0-complete")
    assert complete["active"] == "l0"
    assert complete["signals"] == 1 and complete["completed"]
    assert complete["rip02"] == 0x1002
    assert all(s["rip12"] == 0x1000 for s in states)


def test_userspace_backend_is_distinct_from_nested_reflection(runs):
    states = runs["l0-userspace"]
    assert all(not s["exit12"] and not s["reflections"] and not s["signals"] for s in states)
    qemu = next(s for s in states if s["key"] == "qemu-handle")
    assert qemu["active"] == "qemu" and qemu["userExits"] == 1
    assert qemu["rip02"] == 0x1000 and not qemu["completed"]
    complete = next(s for s in states if s["key"] == "qemu-return")
    assert complete["active"] == "l0" and complete["completed"]
    assert complete["rip02"] == 0x1002 and complete["rip12"] == 0x1000


def test_every_scenario_completes_exactly_one_instruction(runs):
    expected = {"l1-intercept": (1, 0, 0), "l0-eventfd": (0, 1, 0), "l0-userspace": (0, 0, 1)}
    for mode, states in runs.items():
        last = states[-1]
        assert last["done"] and last["completed"] and last["active"] == "l2"
        assert last["rip02"] == 0x1002
        assert (last["reflections"], last["signals"], last["userExits"]) == expected[mode]
        assert not any(s["done"] for s in states[:-1])


def test_back_navigation_bounds_and_pure_model():
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
}
assert.throws(() => m.buildRun('invalid'), RangeError);
for (const index of [-1, 1.5, NaN, 100]) {
  assert.throws(() => m.frame('l1-intercept', index), RangeError);
}
""", str(SCRIPT)],
        check=True, capture_output=True, text=True,
    )


def test_template_has_no_script_fallback_and_accessible_controls():
    text = (ROOT / "virt_report/render/templates/_nested_vmx_lab.html").read_text()
    assert 'data-nested-vmx-controls hidden' in text
    assert 'data-nested-vmx-fallback' in text
    assert 'aria-live="polite"' in text
    assert 'data-mechanism-view="lab"' in text
    assert 'data-mechanism-expand hidden' in text
    assert 'for="nested-vmx-scenario"' in text
    assert '<ol>' in text
