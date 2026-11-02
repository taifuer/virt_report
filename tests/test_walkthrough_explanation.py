"""Shared prose rendering keeps technical detail and avoids duplicate captions."""
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_explanation_preserves_details_and_only_hides_exact_duplicates():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('virt_report/render/assets/walkthrough-components.js', 'utf8'), context);
const nodes = ['description', 'detail', 'state'].map(stepPart => ({dataset: {stepPart}}));
const container = {querySelectorAll: () => nodes};
const render = parts => context.window.WalkthroughComponents.explanation(container, parts);
render({description: '设备写回完成。', detail: '完成不保证持久化。', state: '缓冲区可复用。'});
assert(nodes.every(node => !node.hidden));
assert.equal(nodes[1].textContent, '完成不保证持久化。');
render({description: '发布完成。', detail: '发布完成', state: ''});
assert.deepEqual(nodes.map(node => node.hidden), [false, true, true]);
// A later state must restore a previously omitted span, including in overview.
render({description: '写入返回。', detail: 'F1 仍在刷新。', state: 'R1 与 F1 分别回收。'});
assert(nodes.every(node => !node.hidden));
assert.equal(nodes[1].textContent, 'F1 仍在刷新。');
assert.equal(nodes[2].textContent, 'R1 与 F1 分别回收。');
// Preserve overlapping but distinct statements; do not guess semantic equivalence.
render({description: '已返回', detail: '已返回，但设备仍持有缓冲区。', state: '<b>literal</b>'});
assert(nodes.every(node => !node.hidden));
assert.equal(nodes[2].textContent, '<b>literal</b>');
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
