"""State and ordering regressions for the vIOMMU buffer-remapping example."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "virt_report/render/assets/viommu-lab.js"


@pytest.fixture(scope="module")
def runs():
    if shutil.which("node") is None:
        pytest.skip("Node is optional for browser-model tests")
    result = subprocess.run(
        ["node", "-e", "const m=require(process.argv[1]); console.log(JSON.stringify("
         "Object.fromEntries(m.MODES.map(mode=>[mode,m.buildRun(mode)]))))", str(SCRIPT)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def at(states, key):
    return next(state for state in states if state["key"] == key)


def test_guest_page_table_writes_do_not_implicitly_change_host_or_cache(runs):
    for states in runs.values():
        first = at(states, "guest-map-a")
        assert first["guest"] == "A" and first["pending"]
        assert first["host"] is None and first["cache"] is None
        removed = at(states, "guest-unmap-a")
        assert removed["guest"] is None and removed["pending"]
        assert removed["host"] == removed["cache"] == "A"
        replacement = at(states, "guest-map-b")
        assert replacement["guest"] == "B"
        assert replacement["host"] is None and replacement["cache"] is None


def test_normal_unmap_completion_clears_page_table_and_cache_before_reuse(runs):
    states = runs["correct"]
    removed = at(states, "unmap-complete")
    assert removed["oldRequestDone"] and removed["invalidated"]
    assert removed["host"] is None and removed["cache"] is None
    assert not removed["pending"]
    keys = [state["key"] for state in states]
    assert keys.index("complete-a") < keys.index("guest-unmap-a")
    assert keys.index("unmap-complete") < keys.index("reuse-a") < keys.index("late-probe")
    probe = at(states, "late-probe")
    assert probe["result"]["blocked"] and probe["result"]["destination"] is None
    assert probe["writesA"] == 1
    assert not any(state["corrupted"] for state in states)


def test_missing_guest_invalidation_never_calls_host_unmap_before_bad_access(runs):
    states = runs["missing-invalidation"]
    probe = at(states, "late-probe")
    assert probe["guest"] is None
    assert probe["host"] == probe["cache"] == "A"
    assert not probe["invalidated"] and probe["pending"]
    assert probe["result"]["cacheHit"] and probe["result"]["wrongOwner"]
    assert probe["result"]["destination"] == "0x120000080"
    assert probe["writesA"] == 2 and probe["corrupted"]
    repair = at(states, "repair-invalidation")
    assert repair["invalidated"] and not repair["pending"]
    assert repair["host"] is None and repair["cache"] is None
    assert repair["corrupted"] and repair["writesA"] == 2


def test_same_iova_changes_physical_destination_only_after_new_mapping(runs):
    for states in runs.values():
        a = at(states, "dma-a")
        b = at(states, "dma-b")
        assert a["result"]["destination"] == "0x120000080"
        assert b["result"]["destination"] == "0x140000080"
        assert not a["result"]["cacheHit"] and not b["result"]["cacheHit"]
        assert b["host"] == b["guest"] == b["cache"] == "B"
        assert b["writesB"] == 1 and b["done"]


def test_model_is_immutable_and_back_navigation_restores_every_state():
    if shutil.which("node") is None:
        pytest.skip("Node is optional for browser-model tests")
    subprocess.run(
        ["node", "-e", """
const assert = require('node:assert/strict');
const m = require(process.argv[1]);
for (const mode of m.MODES) {
  let state = m.frame(mode, 0);
  const history = [state];
  while (!state.done) {
    const before = JSON.stringify(state);
    const next = m.step(state);
    assert.equal(JSON.stringify(state), before);
    state = next; history.push(state);
  }
  assert.deepEqual(m.step(state), state);
  for (let i = history.length - 2; i >= 0; i--) {
    state = m.back(state); assert.deepEqual(state, history[i]);
  }
  assert.deepEqual(m.back(state), state);
}
assert.throws(() => m.buildRun('unknown'), RangeError);
for (const index of [-1, 0.5, NaN, 999]) {
  assert.throws(() => m.frame('correct', index), RangeError);
}
const s = {host:'B', cache:'A', aReused:true};
const frozen = JSON.stringify(s);
assert.equal(m.dma(s).page, 'A');
assert.equal(m.dma(s).wrongOwner, true);
assert.equal(JSON.stringify(s), frozen);
""", str(SCRIPT)],
        check=True, capture_output=True, text=True,
    )
