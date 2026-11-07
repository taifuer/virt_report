"""Verify pixel ownership, partial-copy geometry, and display completion boundaries."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "virt_report/render/assets/virtio-gpu-lab.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for model tests")
def test_gpu_model_separates_backing_image_and_display_consumption():
    program = r"""
const assert = require('node:assert/strict');
const m = require(process.argv[1]);
for (const size of ['small', 'large']) {
  const r = m.rectangle(size);
  assert.equal(r.offset, 393728);
  assert.equal(r.bytes, size === 'small' ? 32768 : 131072);
  const rows = m.rowCopies(r);
  assert.equal(rows.length, r.height);
  assert.equal(rows.reduce((sum, row) => sum + row.length, 0), r.bytes);
  for (const [index, row] of rows.entries()) {
    assert.equal(row.source, 393728 + index * 4096);
    assert.equal(row.destination, row.source);
    assert.equal(row.length, r.width * 4);
  }
  assert.equal(m.frame(size, 2).hostVersion, 'allocated');
  assert.equal(m.frame(size, 2).totalCopiedBytes, 0, 'ATTACH does not copy pixels');
  assert.equal(m.frame(size, 3).hostVersion, 'A');
  assert.equal(m.frame(size, 3).copiedBytes, 3 * 1048576);
  assert.equal(m.frame(size, 4).scanout, true);
  assert.equal(m.frame(size, 4).copiedBytes, 0, 'SET_SCANOUT does not copy pixels');
  assert.equal(m.frame(size, 6).guestVersion, 'B');
  assert.equal(m.frame(size, 6).hostVersion, 'A');
  assert.equal(m.frame(size, 7).hostVersion, 'B');
  assert.equal(m.frame(size, 7).displayVersion, 'A');
  assert.equal(m.frame(size, 7).copiedBytes, r.bytes);
  assert.equal(m.frame(size, 8).updatePending, true);
  assert.equal(m.frame(size, 8).copiedBytes, 0, 'FLUSH notifies, not a backing copy');
  assert.equal(m.frame(size, 9).fenceReturned, true);
  assert.equal(m.frame(size, 9).displayVersion, 'A');
  assert.equal(m.frame(size, 10).displayVersion, 'B');
  assert.equal(m.frame(size, 10).physicalPresentationKnown, false);
  assert.equal(m.frame(size, 10).totalCopiedBytes, 3 * 1048576 + r.bytes);
  let s = m.frame(size);
  for (let i = 1; i <= m.LAST_STEP; i++) {
    const before = JSON.stringify(s);
    const next = m.step(s);
    assert.equal(JSON.stringify(s), before);
    assert.deepEqual(m.back(next), s);
    s = next;
  }
  assert.deepEqual(m.step(s), s);
  assert.deepEqual(m.back(m.frame(size)), m.frame(size));
}
assert.throws(() => m.frame('bad'), RangeError);
for (const i of [-1, .5, 11, NaN]) assert.throws(() => m.frame('small', i), RangeError);
for (const changes of [{x:-1}, {width:0}, {width:1024}, {height:768}, {offset:3145728}]) {
  assert.throws(() => m.rowCopies({...m.rectangle(), ...changes}), RangeError);
}
"""
    result = subprocess.run(
        ["node", "-e", program, str(ASSET)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gpu_lab_has_non_javascript_explanation_and_accessible_controls():
    template = (ROOT / "virt_report/render/templates/_virtio_gpu_lab.html").read_text()
    assert 'data-mechanism-view="lab"' in template
    assert "data-gpu-fallback" in template
    assert 'aria-live="polite"' in template
    for control in ("size", "back", "next", "reset", "guest-state", "host-state", "display-state"):
        assert f"data-gpu-{control}" in template
    assert "并非额外复制的一份 framebuffer" in template
    assert "启用 JavaScript" in template


def test_gpu_source_links_pin_qemu_and_do_not_claim_fence_is_presentation():
    source = (ROOT / "virt_report/content/articles/virtio-gpu.md").read_text()
    assert "698104725efad4b29079d857dfdebbd804e34c99" in source
    assert "不能证明远程客户端已经收到图像" in source
    assert "393728 B" in source
    assert "180 MiB/s" in source
    assert "## 从应用到虚拟设备与宿主 GPU {#preface}" in source
