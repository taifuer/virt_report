"""State invariants for DMA translation and synchronized mapping teardown."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional")
def test_iommu_lab_states_and_safe_reclamation():
    script = Path(__file__).parents[1] / "virt_report/render/assets/iommu-lab.js"
    result = subprocess.run(
        ["node", "-e", r"""
const assert = require('node:assert/strict');
const model = require(process.argv[1]);
for (const scenario of model.SCENARIOS) {
  let state = model.frame(scenario);
  const frames = [state];
  while (!state.done) {
    const before = JSON.stringify(state);
    const next = model.step(state);
    assert.equal(JSON.stringify(state), before);
    state = next;
    frames.push(state);
    if (state.dmaWritten) {
      assert.ok(state.checked && !state.fault);
      assert.equal(state.target, 0x21000080);
      assert.equal(state.payloadBytes, 64);
    }
    if (state.index >= 9) assert.ok(state.quiesced);
    if (state.invalidated) assert.equal(state.cached, false);
    if (state.released) {
      assert.ok(state.quiesced && state.invalidated);
      assert.equal(state.mapped, false);
      assert.equal(state.cached, false);
    }
  }
  if (scenario === 'readonly' || scenario === 'missing') {
    assert.equal(state.index, 5);
    assert.equal(state.fault, true);
    assert.equal(state.payloadBytes, 0);
    assert.equal(state.memory, 'A');
    assert.equal(state.received, false);
    assert.equal(state.released, false);
    assert.equal(state.target, null);
  } else {
    assert.equal(state.index, 11);
    assert.equal(state.memory, 'B');
    assert.equal(state.released, true);
  }
  assert.deepEqual(model.step(state), state);
  for (let index = frames.length - 2; index >= 0; index--) {
    state = model.back(state);
    assert.deepEqual(state, frames[index]);
  }
  assert.deepEqual(model.back(state), model.frame(scenario));
}
assert.equal(model.frame('cold', 3).cacheHit, false);
assert.equal(model.frame('cold', 4).walked, true);
assert.equal(model.frame('warm', 3).cacheHit, true);
assert.equal(model.frame('warm', 4).walked, false);
assert.equal(model.frame('cold', 9).mapped, false);
assert.equal(model.frame('cold', 9).cached, true);
assert.equal(model.frame('cold', 9).released, false);
assert.equal(model.frame('cold', 10).cached, false);
for (const index of [-1, 0.5, 12, NaN]) assert.throws(() => model.frame('cold', index), RangeError);
assert.throws(() => model.frame('invalid'), RangeError);
""", str(script)],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
