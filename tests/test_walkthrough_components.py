"""Shared components preserve domain positions and protocol-state boundaries."""
import shutil
import subprocess

import pytest


DOM = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {};
    this.attributes = {}; this.classList = {add() {}};
  }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent = this; } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = value; }
  all(predicate) { return this.children.flatMap(node => [...(predicate(node) ? [node] : []), ...node.all(predicate)]); }
}
const context = {window: {}, document: {createElement: tag => new Element(tag)}};
vm.runInNewContext(fs.readFileSync('virt_report/render/assets/walkthrough-components.js', 'utf8'), context);
const components = context.window.WalkthroughComponents;
"""


def run_component_test(script: str) -> None:
    if shutil.which("node") is None:
        pytest.skip("Node.js is required")
    result = subprocess.run(["node", "-e", DOM + script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_components_have_fixed_positions_even_when_domains_are_reordered_or_idle():
    run_component_test(r"""
const base = ['guest', 'kvm', 'qemu', 'host-kernel', 'hardware'];
const groups = [...base, 'host-user', 'external'].map((component, id) => ({
  id: String(id), component, title: component, context: 'context', nodes: [], note: 'background',
}));
groups.push({id: 'guest-driver', component: 'guest', context: 'kernel', nodes: ['driver']});
const render = order => {
  const root = new Element('div'), visited = [];
  components.componentLayout(root, order, group => {
    visited.push(group.id); const node = new Element('div'); node.dataset.group = group.id; return node;
  });
  assert.deepEqual(root.children.map(n => n.dataset.componentColumn), base);
  assert.deepEqual(root.children.map(n => n.children[0].children[1].textContent),
    ['Guest', 'KVM', 'QEMU', 'Host kernel', '物理硬件']);
  assert.deepEqual(visited.sort(), groups.map(g => g.id).sort(), 'every domain, including backgrounds, is rendered once');
  for (const extra of root.all(n => ['host-user', 'external'].includes(n.dataset.component))) {
    assert.equal(extra.parent.dataset.component, undefined, 'separate processes are not children of QEMU or hardware');
    assert.equal(extra.parent.children[0].dataset.component, extra.dataset.component === 'host-user' ? 'qemu' : 'hardware');
  }
};
render(groups); render([...groups].reverse());
""")


def test_descriptor_allocation_does_not_highlight_used_ring():
    run_component_test(r"""
const empty = {index: 0, consumer: 0, slots: Array(8).fill(null)};
const state = {descriptors: [], buffers: [], freeDescriptors: 8, avail: empty, used: empty, changed: []};
const render = state => {
  const root = new Element('div'); components.queueMemory(root, state, {name: 'TX', size: 8});
  const panels = root.children[0].children;
  assert.deepEqual(panels.map(p => p.children[0].textContent),
    ['Descriptor table', 'Buffers', 'Available ring', 'Used ring']);
  const free = root.all(n => n.dataset.freeDescriptors !== undefined);
  assert.equal(free.length, 1); assert.equal(free[0].parent.dataset.networkMemoryPart, 'descriptors');
  assert.equal(free[0].textContent, `Free descriptors ${state.freeDescriptors} / 8`);
  return panels.map(p => p.dataset.current);
};
assert.deepEqual(render(state), ['false', 'false', 'false', 'false']);
// One virtio-net header and one Ethernet frame: reserve two direct descriptors.
state.descriptors = [
  {index: 0, addr: 0x1000, len: 12, flags: ['NEXT'], next: 2},
  {index: 2, addr: 0x2000, len: 60, flags: [], next: null},
];
state.buffers = [{id: 'header', addr: 0x1000, label: 'header', length: 12, validBytes: 12, state: 'prepared'},
  {id: 'frame', addr: 0x2000, label: 'frame', length: 60, validBytes: 60, state: 'prepared'}];
state.freeDescriptors = 6; state.changed = ['descriptors', 'buffers', 'freeDescriptors'];
assert.deepEqual(render(state), ['true', 'true', 'false', 'false']);
assert.equal(state.used, empty); assert.equal(state.avail, empty);
// Even a local allocator-only change is unrelated to Used ring.
state.changed = ['freeDescriptors'];
assert.deepEqual(render(state), ['true', 'false', 'false', 'false']);
state.used = {index: 0, consumer: 0, slots: [{id: 0, len: 0}, ...Array(7).fill(null)]};
state.changed = ['used.slots'];
assert.deepEqual(render(state), ['false', 'false', 'false', 'true']);
// A block response/status is pointed-to buffer data, not a Used ring field.
state.response = {ready: true, kind: 'blk', status: 'OK'}; state.changed = ['response'];
assert.deepEqual(render(state), ['false', 'true', 'false', 'false']);
""")
