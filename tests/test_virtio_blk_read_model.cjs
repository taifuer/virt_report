'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {validateScenario, buildTrace, getScenario} = require('../virt_report/render/assets/virtio-blk-read-model.js');
const source = JSON.parse(fs.readFileSync(path.join(__dirname,
  '../virt_report/content/flows/virtio-blk-read.json'), 'utf8'));
const fresh = () => structuredClone(source);
const frame = (trace, id) => trace.find(item => item.id === id);
const step = (scenario, id) => frame(scenario.steps, id);

// Synthetic full scenes exercise the catalog contract without network or collector fixtures.
function catalogScene(backend, operation, transport = 'virtio-blk') {
  const scene = getScenario(source, operation);
  delete scene.operationVariants;
  scene.id = `${backend}-${operation}`;
  const scsi = transport === 'virtio-scsi';
  Object.assign(scene.request, {backend, interface: transport,
    command: scsi ? `${operation.toUpperCase()}(10)` : operation === 'read' ? 'IN' : 'OUT',
    headerBytes: scsi ? 51 : 16, responseBytes: scsi ? 108 : 1,
    usedLength: (scsi ? 108 : 1) + (operation === 'read' ? 4096 : 0)});
  if (scsi) {
    scene.request.descriptors = operation === 'read' ? [
      {id: 0, addr: '0x1000', len: 51, flags: ['NEXT'], next: 1, role: 'SCSI request'},
      {id: 1, addr: '0x3000', len: 108, flags: ['NEXT', 'WRITE'], next: 2, role: 'SCSI response'},
      {id: 2, addr: '0x2000', len: 4096, flags: ['WRITE'], next: null, role: 'READ data'},
    ] : [
      {id: 0, addr: '0x1000', len: 51, flags: ['NEXT'], next: 1, role: 'SCSI request'},
      {id: 1, addr: '0x2000', len: 4096, flags: ['NEXT'], next: 2, role: 'WRITE data'},
      {id: 2, addr: '0x3000', len: 108, flags: ['WRITE'], next: null, role: 'SCSI response'},
    ];
  }
  step(scene, 'write-used').changes.usedLength = scene.request.usedLength;
  scene.presentation = {deviceTitle: scsi ? 'virtio-scsi' : 'virtio-blk',
    notificationMode: backend.startsWith('spdk') ? 'poll' : 'eventfd'};
  return scene;
}

function fakeCatalog() {
  return {routes: [
    ['local-scsi', 'virtio-scsi'], ['qemu-rbd', 'virtio-blk'],
    ['spdk-blk', 'virtio-blk'], ['spdk-scsi', 'virtio-scsi'],
  ].map(([id, transport]) => ({id, label: id, scenarios: {
    read: catalogScene(id, 'read', transport), write: catalogScene(id, 'write', transport),
  }}))};
}

test('full backend catalogs provide independent read and write scenes without changing local defaults', () => {
  const catalog = fakeCatalog(), before = JSON.stringify(catalog);
  assert.deepEqual(getScenario(source, 'read', 'local-blk', catalog), getScenario(source));
  assert.deepEqual(getScenario(source, 'write', 'local-blk', catalog), getScenario(source, 'write'));
  for (const route of catalog.routes) {
    for (const operation of ['read', 'write']) {
      const scene = getScenario(source, operation, route.id, catalog);
      assert.equal(scene.request.backend, route.id);
      assert.equal(scene.request.operation, operation);
      assert.equal(buildTrace(scene).at(-1).state.userReturned, true);
      scene.presentation.deviceTitle = 'changed';
      scene.request.descriptors[0].flags.push('bad');
      scene.steps[1].changes.extra = true;
      assert.equal(validateScenario(getScenario(source, operation, route.id, catalog)), true);
    }
  }
  assert.equal(JSON.stringify(catalog), before);
});

test('SCSI uses request/response/data order for READ and request/data/response for WRITE', () => {
  const catalog = fakeCatalog();
  const read = getScenario(source, 'read', 'local-scsi', catalog);
  const write = getScenario(source, 'write', 'local-scsi', catalog);
  assert.deepEqual(read.request.descriptors.map(item => item.len), [51, 108, 4096]);
  assert.deepEqual(write.request.descriptors.map(item => item.len), [51, 4096, 108]);
  assert.deepEqual(read.request.descriptors.map(item => item.flags),
    [['NEXT'], ['NEXT', 'WRITE'], ['WRITE']]);
  assert.deepEqual(write.request.descriptors.map(item => item.flags),
    [['NEXT'], ['NEXT'], ['WRITE']]);
  assert.equal(buildTrace(read).at(-1).state.usedLength, 4204);
  assert.equal(buildTrace(write).at(-1).state.usedLength, 108);
  delete read.request.usedLength;
  assert.equal(validateScenario(read), true);
  read.request.completionBytes = read.request.responseBytes;
  delete read.request.responseBytes;
  assert.equal(validateScenario(read), true);
});

test('explicit backend completion accounting is separate from descriptor capacity', () => {
  const scene = catalogScene('spdk-scsi', 'read', 'virtio-scsi');
  scene.request.usedLength = 4096;
  step(scene, 'write-used').changes.usedLength = 4096;
  assert.equal(validateScenario(scene), true);
  assert.equal(buildTrace(scene).at(-1).state.usedLength, 4096);
});

test('QEMU SCSI WRITE can report payload plus response, not only device-writable bytes', () => {
  const scene = catalogScene('local-scsi', 'write', 'virtio-scsi');
  scene.request.usedLength = 4204;
  scene.request.usedLengthNote = 'QEMU virtio_scsi_complete_req reports qsgl.size + resp_iov.size';
  step(scene, 'write-used').changes.usedLength = 4204;
  assert.equal(scene.request.descriptors[2].len, 108);
  assert.deepEqual(scene.request.descriptors[1].flags, ['NEXT']);
  assert.equal(validateScenario(scene), true);
  assert.equal(buildTrace(scene).at(-1).state.usedLength, 4204);
});

test('SPDK polling observation needs no ioeventfd function or QEMU actor', () => {
  const scene = catalogScene('spdk-blk', 'read');
  const commit = 'a'.repeat(40);
  scene.baselines.push({project: 'SPDK', version: 'test-fixture', commit,
    url: `https://github.com/spdk/spdk/tree/${commit}`});
  scene.sources['fixture-poll'] = {project: 'SPDK', path: 'lib/vhost/vhost_blk.c',
    symbol: 'fixture_queue_poll', line: 1,
    url: `https://github.com/spdk/spdk/blob/${commit}/lib/vhost/vhost_blk.c#L1`};
  const qemu = scene.lanes.find(lane => lane.id === 'qemu');
  Object.assign(qemu, {id: 'spdk', title: 'SPDK', subtitle: 'poller'});
  scene.steps.forEach(item => {if (item.actor === 'qemu') item.actor = 'spdk';});
  const observed = step(scene, 'signal-ioeventfd');
  observed.id = 'observe-available'; observed.actor = 'spdk';
  observed.title = 'Poller observes published work';
  observed.sourceIds = ['fixture-poll']; observed.edge = null;
  assert.equal(validateScenario(scene), true);
  const trace = buildTrace(scene);
  assert.equal(frame(trace, 'observe-available').state.notified, true);
  assert.equal(frame(trace, 'observe-available').state.consumed, false);
  assert.equal(frame(trace, 'consume-chain').state.consumed, true);
  scene.sources['fixture-poll'].url = scene.sources['fixture-poll'].url.replace(commit, 'master');
  assert.throws(() => validateScenario(scene), /source URL/);
});

test('Ceph actors and pinned Ceph source repositories are allowed when declared', () => {
  const scene = catalogScene('qemu-rbd', 'read');
  const commit = 'b'.repeat(40);
  scene.baselines.push({project: 'Ceph', version: 'test-fixture', commit,
    url: `https://github.com/ceph/ceph/tree/${commit}`});
  scene.sources['fixture-rbd'] = {project: 'Ceph', path: 'src/librbd/io/AioCompletion.cc',
    symbol: 'fixture_complete', line: 1,
    url: `https://github.com/ceph/ceph/blob/${commit}/src/librbd/io/AioCompletion.cc#L1`};
  scene.lanes.push({id: 'ceph', title: 'Ceph', subtitle: 'RBD cluster'});
  step(scene, 'read-data').actor = 'ceph';
  step(scene, 'read-data').sourceIds = ['fixture-rbd'];
  assert.equal(validateScenario(scene), true);
});

for (const [label, mutate, expected] of [
  ['duplicate route', c => {c.routes[1].id = c.routes[0].id;}, /duplicates/],
  ['unsafe route id', c => {c.routes[1].id = '../secret';}, /route id/],
  ['missing operation', c => {delete c.routes[0].scenarios.read;}, /missing backend operation/],
  ['backend mismatch', c => {c.routes[0].scenarios.read.request.backend = 'qemu-rbd';}, /scenario identity/],
  ['operation mismatch', c => {c.routes[0].scenarios.read.request.operation = 'write';}, /scenario identity/],
  ['missing interface', c => {delete c.routes[0].scenarios.read.request.interface;}, /catalog request interface/],
  ['invalid interface', c => {c.routes[0].scenarios.read.request.interface = 'network';}, /request interface/],
  ['unsafe scene id', c => {c.routes[0].scenarios.read.id = '../../read';}, /scenario id/],
  ['bad SCSI response size', c => {c.routes[0].scenarios.read.request.descriptors[1].len = 1;}, /direct chain identity/],
  ['bad SCSI data permissions', c => {c.routes[0].scenarios.read.request.descriptors[2].flags = [];}, /permissions/],
  ['SCSI response size alias conflict', c => {c.routes[0].scenarios.read.request.completionBytes = 1;}, /completion bytes mismatch/],
  ['negative explicit completion', c => {c.routes[0].scenarios.read.request.usedLength = -1;}, /request used length/],
  ['oversized explicit completion', c => {c.routes[0].scenarios.read.request.usedLength = 1000000;}, /request used length/],
  ['unsafe pinned source', c => {c.routes[0].scenarios.read.sources['guest-submit'].url = 'javascript:alert(1)';}, /source URL/],
  ['undeclared actor', c => {c.routes[0].scenarios.read.steps[0].actor = 'spdk';}, /step actor/],
]) {
  test(`rejects catalog ${label}`, () => {
    const catalog = fakeCatalog(); mutate(catalog);
    assert.throws(() => getScenario(source, 'read', 'local-scsi', catalog), expected);
  });
}

test('nonlocal selection rejects missing catalog and unknown routes', () => {
  assert.throws(() => getScenario(source, 'read', 'local-scsi'), /backend catalog/);
  assert.throws(() => getScenario(source, 'read', 'missing', fakeCatalog()), /unknown requested backend/);
});

test('reviewed scene covers user pread through return in ten phases with pinned evidence', () => {
  assert.equal(validateScenario(source), true);
  const trace = buildTrace(source);
  assert.equal(trace.length, 28);
  assert.equal(new Set(trace.map(step => step.phase)).size, 10);
  assert(trace.every(step => step.sourceIds.length > 0));
  assert.equal(trace[0].state.prepared, false);
  assert.equal(trace.at(-1).state.completed, true);
  assert.equal(trace[0].actor, 'guest-user');
  assert.equal(trace.at(-1).state.userReturned, true);
  assert.equal(trace.at(-1).actor, 'guest-user');
  const pathSources = new Set(source.callPaths.flatMap(path => path.sourceIds));
  assert(trace.every(item => item.sourceIds.some(id => pathSources.has(id))));
});

test('syscall, pinned user pages and block submission precede virtqueue publication', () => {
  const trace = buildTrace(source);
  assert.equal(trace[0].state.syscallEntered, false);
  assert.equal(frame(trace, 'enter-syscall').state.syscallEntered, true);
  assert.equal(frame(trace, 'enter-syscall').state.pagesMapped, false);
  assert.equal(frame(trace, 'map-direct-buffer').state.pagesMapped, true);
  assert.equal(frame(trace, 'map-direct-buffer').state.blockSubmitted, false);
  assert.equal(frame(trace, 'submit-bio').state.blockSubmitted, true);
  assert.equal(frame(trace, 'submit-bio').state.prepared, false);
  assert.equal(frame(trace, 'ready').state.pagesMapped, true);
  assert.equal(frame(trace, 'prepare-descriptors').state.prepared, true);
});

test('bio completion inside block completion precedes the modeled application return', () => {
  const trace = buildTrace(source);
  const bio = frame(trace, 'complete-bio').state;
  assert.equal(bio.reclaimed, true);
  assert.equal(bio.bioCompleted, true);
  assert.equal(bio.completed, false);
  assert.equal(bio.userReturned, false);
  const block = frame(trace, 'finish-request').state;
  assert.equal(block.completed, true);
  assert.equal(block.userReturned, false);
  assert.equal(frame(trace, 'return-to-user').state.userReturned, true);
  assert.deepEqual(frame(trace, 'return-to-user').edge,
    {from: 'driver', to: 'application', kind: 'metadata', label: '返回 4096 B'});
});

test('call paths separate submission, worker, callbacks and return with pinned sources', () => {
  assert.equal(source.callPaths.length, 10);
  assert(source.callPaths.every(path => path.context && path.boundary));
  const paths = Object.fromEntries(source.callPaths.map(path => [path.id, path]));
  assert(paths['guest-submission'].sourceIds.includes('guest-bio-wait'));
  assert.deepEqual(paths['host-worker-read'].sourceIds, ['file-worker', 'file-pread']);
  assert(!paths['qemu-submission'].sourceIds.includes('read-complete'));
  assert(!paths['guest-interrupt'].sourceIds.includes('guest-bio-end'));
  assert(paths['guest-bio-completion'].sourceIds.includes('guest-wait-complete'));
  assert.match(paths['application-resume'].boundary, /返回方向/);
  assert.deepEqual(paths['kvm-vmentry-injection'].sourceIds,
    ['event-inject', 'vmx-inject', 'vmx-entry']);
});

test('irqfd, routing, LAPIC pending, VM-entry injection and Guest callback are distinct', () => {
  const trace = buildTrace(source);
  const sequence = [
    ['signal-irqfd', 'interruptSignaled'], ['route-interrupt', 'irqRouted'],
    ['pending-interrupt', 'irqPending'], ['inject-interrupt', 'irqInjected'],
    ['guest-notice', 'guestNoticed'],
  ];
  sequence.forEach(([id, flag], index) => {
    const current = frame(trace, id);
    assert.equal(current.state[flag], true);
    if (index + 1 < sequence.length) assert.equal(current.state[sequence[index + 1][1]], false);
    if (index) assert(current.index > frame(trace, sequence[index - 1][0]).index);
  });
  assert.match(step(source, 'inject-interrupt').detail, /VM_ENTRY_INTR_INFO_FIELD/);
  assert.match(step(source, 'inject-interrupt').detail, /成功 VM-entry/);
  assert.match(step(source, 'pending-interrupt').detail, /IF=0/);
});

test('operation derivation is deterministic, isolated and preserves the READ base', () => {
  const before = JSON.stringify(source);
  const read = getScenario(source);
  const write = getScenario(source, 'write');
  assert.deepEqual(read, source);
  assert.equal(write.id, 'virtio-blk-write');
  assert.equal(write.request.operation, 'write');
  assert.equal(write.steps.length, 29);
  assert.deepEqual(write, getScenario(source, 'write'));
  write.request.descriptors[1].flags.push('WRITE');
  write.callPaths[0].sourceIds.push('bad');
  write.steps[1].changes.bufferReady = false;
  read.initial.bufferReady = true;
  assert.equal(JSON.stringify(source), before);
  assert.equal(validateScenario(getScenario(source, 'write')), true);
});

test('WRITE prepares payload before syscall and only status is device-writable', () => {
  const scene = getScenario(source, 'write');
  const trace = buildTrace(scene);
  assert.deepEqual(scene.request.descriptors.map(item => item.flags),
    [['NEXT'], ['NEXT'], ['WRITE']]);
  const prepared = frame(trace, 'prepare-write-buffer').state;
  assert.equal(prepared.bufferReady, true);
  assert.equal(prepared.syscallEntered, false);
  assert.equal(prepared.dataReady, false);
  const backend = frame(trace, 'read-data');
  assert.equal(backend.state.bufferReady, true);
  assert.equal(backend.state.dataReady, true);
  assert.deepEqual(backend.edge,
    {from: 'buffer', to: 'storage', kind: 'data', label: '消费 4096 B payload'});
  assert.equal(frame(trace, 'write-used').state.usedLength, 1);
  assert.equal(trace.at(-1).state.userReturned, true);
  assert.match(step(scene, 'return-to-user').detail, /不等于断电安全/);
  assert(scene.assumptions.some(value => /没有 FLUSH、FUA/.test(value)));
});

test('WRITE uses verified write sources and independent async execution paths', () => {
  const scene = getScenario(source, 'write');
  assert.deepEqual(step(scene, 'enter-syscall').sourceIds,
    ['guest-pwrite', 'guest-vfs-write', 'guest-sync-write', 'guest-write-iter']);
  assert.deepEqual(step(scene, 'submit-read').sourceIds,
    ['write-submit', 'block-write', 'raw-write-format']);
  assert.deepEqual(step(scene, 'read-data').sourceIds,
    ['file-read', 'file-worker', 'file-pwrite']);
  const paths = new Set(scene.callPaths.flatMap(path => path.sourceIds));
  assert(scene.steps.every(item => item.sourceIds.some(id => paths.has(id))));
  assert.equal(scene.sources['guest-pwrite'].symbol, 'ksys_pwrite64');
  assert.equal(scene.sources['block-write'].symbol, 'blk_aio_write_entry');
  assert(!scene.callPaths.find(path => path.id === 'qemu-submission').sourceIds.includes('read-complete'));
});

for (const [label, mutate, expected] of [
  ['unknown step patch', s => {s.operationVariants.write.stepPatches.missing = {title: 'bad'};}, /unknown variant step/],
  ['patch changes step identity', s => {s.operationVariants.write.stepPatches.ready.id = 'bad';}, /step patch keys/],
  ['prototype patch key', s => {s.operationVariants.write.stepPatches.ready = JSON.parse('{"__proto__":{"bad":true}}');}, /step patch keys/],
  ['unknown variant source', s => {s.operationVariants.write.sourceMap['guest-pread'] = 'missing';}, /unknown variant source/],
  ['unknown variant phase', s => {s.operationVariants.write.phasePatches.missing = {title: 'bad'};}, /unknown variant phase/],
  ['unknown insertion anchor', s => {s.operationVariants.write.insertSteps[0].after = 'missing';}, /unknown insertion anchor/],
  ['WRITE data permission', s => {s.operationVariants.write.descriptors[1].flags.push('WRITE');}, /permissions/],
  ['WRITE used length', s => {s.operationVariants.write.stepPatches['write-used'].changes.usedLength = 4097;}, /used length/],
  ['WRITE missing payload', s => {s.operationVariants.write.insertSteps[0].step.changes = {};}, /prepared payload/],
]) {
  test(`rejects ${label}`, () => {
    const scene = fresh(); mutate(scene);
    assert.throws(() => getScenario(scene, 'write'), expected);
  });
}

test('rejects unsupported operations and refuses to derive from an already WRITE base', () => {
  assert.throws(() => getScenario(source, 'flush'), /requested operation/);
  assert.throws(() => getScenario(getScenario(source, 'write'), 'write'), /base must be READ/);
});

test('READ makes result and status buffers device-writable, not the command header', () => {
  assert.deepEqual(source.request.descriptors.map(item => item.flags),
    [['NEXT'], ['NEXT', 'WRITE'], ['WRITE']]);
  assert.deepEqual(source.request.descriptors.map(item => item.next), [1, 2, null]);
  const scene = fresh();
  scene.request.descriptors[1].flags = ['NEXT'];
  assert.throws(() => validateScenario(scene), /permissions/);
});

test('publication precedes notification, and consuming available does not complete a request', () => {
  const trace = buildTrace(source);
  const published = frame(trace, 'publish-available').state;
  assert.equal(published.availIndex, 1);
  assert.equal(published.notified, false);
  const signaled = frame(trace, 'signal-ioeventfd').state;
  assert.equal(signaled.notified, true);
  assert.equal(signaled.consumed, false);
  const consumed = frame(trace, 'consume-chain').state;
  assert.equal(consumed.backendIndex, 1);
  assert.equal(consumed.usedIndex, 0);
  assert.equal(consumed.freeDescriptors, 5);
  assert.equal(consumed.dataReady, false);
});

test('data then status then used record then used index become visible in distinct steps', () => {
  const trace = buildTrace(source);
  const data = frame(trace, 'read-data').state;
  assert.equal(data.dataReady, true);
  assert.equal(data.statusWritten, false);
  const status = frame(trace, 'write-status').state;
  assert.equal(status.statusByte, '0x00');
  assert.equal(status.usedId, null);
  const used = frame(trace, 'write-used').state;
  assert.deepEqual([used.usedId, used.usedLength, used.usedIndex], [0, 4097, 0]);
  const published = frame(trace, 'publish-used').state;
  assert.equal(published.usedIndex, 1);
  assert.equal(published.interruptSignaled, false);
  assert.equal(published.freeDescriptors, 5);
});

test('interrupt notification, Guest observation, descriptor reclaim and block completion differ', () => {
  const trace = buildTrace(source);
  const signaled = frame(trace, 'signal-irqfd').state;
  assert.equal(signaled.interruptSignaled, true);
  assert.equal(signaled.guestNoticed, false);
  assert.equal(frame(trace, 'guest-notice').state.reclaimed, false);
  const reclaimed = frame(trace, 'reclaim-chain').state;
  assert.deepEqual([reclaimed.driverIndex, reclaimed.freeDescriptors], [1, 8]);
  assert.equal(reclaimed.completed, false);
  assert.deepEqual([reclaimed.usedId, reclaimed.usedLength, reclaimed.usedIndex], [0, 4097, 1],
    'reclaim does not clear shared completion records');
  assert.equal(trace.at(-1).state.completed, true);
});

test('snapshots support deterministic backward/forward replay without state leakage', () => {
  const before = JSON.stringify(source);
  const a = buildTrace(source), b = buildTrace(source);
  assert.deepEqual(a, b);
  const published = frame(a, 'publish-available');
  published.state.availIndex = 9;
  published.active.push('bad');
  published.sourceIds.push('bad');
  published.changes.published = false;
  published.edge.label = 'changed';
  assert.equal(frame(a, 'check-kick').state.availIndex, 1);
  assert.equal(frame(b, 'publish-available').state.availIndex, 1);
  assert.equal(frame(b, 'publish-available').edge.label, '发布链头');
  assert.equal(JSON.stringify(source), before);
  const indexes = [25, 0, 18, 7, 22, 1, 25];
  for (const index of indexes) assert.deepEqual(buildTrace(source)[index], b[index]);
});

for (const [label, mutate, expected] of [
  ['unsafe source URL', s => {s.sources['guest-submit'].url = 'javascript:alert(1)';}, /source URL/],
  ['moving source ref', s => {s.sources['guest-submit'].url = s.sources['guest-submit'].url.replace(s.baselines[1].commit, 'master');}, /source URL/],
  ['source traversal', s => {s.sources['guest-submit'].path = '../secret';}, /source path/],
  ['baseline URL mismatch', s => {s.baselines[0].url = 'https://evil.example/tree/' + s.baselines[0].commit;}, /baseline URL/],
  ['duplicate step', s => {s.steps[1].id = s.steps[0].id;}, /duplicates/],
  ['unknown source', s => {s.steps[0].sourceIds = ['missing'];}, /unknown source/],
  ['unknown active node', s => {s.steps[0].active = ['missing'];}, /unknown active node/],
  ['missing edge', s => {delete s.steps[0].edge;}, /edge must be explicit/],
  ['unknown edge endpoint', s => {s.steps[1].edge.to = 'unknown';}, /edge endpoints/],
  ['self-referencing edge', s => {s.steps[1].edge.to = s.steps[1].edge.from;}, /edge endpoints/],
  ['unknown edge kind', s => {s.steps[1].edge.kind = 'magic';}, /edge kind/],
  ['empty edge label', s => {s.steps[1].edge.label = '';}, /edge label/],
  ['extra edge property', s => {s.steps[1].edge.url = 'javascript:alert(1)';}, /edge keys/],
  ['unknown patch key', s => {s.steps[1].changes.extra = 1;}, /unknown state change/],
  ['prototype pollution key', s => {s.steps[1].changes = JSON.parse('{"__proto__":{"bad":true}}');}, /unknown state change/],
  ['wrong state type', s => {step(s, 'prepare-descriptors').changes.prepared = 'true';}, /prepared/],
  ['wrong used length', s => {step(s, 'write-used').changes.usedLength = 4096;}, /used length/],
  ['premature reclaim', s => {step(s, 'write-status').changes.reclaimed = true;}, /requires|ownership/],
  ['overlapping buffers', s => {s.request.descriptors[2].addr = '0x2100';}, /overlapping/],
  ['reverse phase', s => {step(s, 'finish-request').phase = 'prepare';}, /phase order/],
  ['unfinished request', s => {s.steps.at(-1).changes = {};}, /must finish/],
  ['missing call paths', s => {delete s.callPaths;}, /call paths/],
  ['duplicate call path id', s => {s.callPaths[1].id = s.callPaths[0].id;}, /duplicates/],
  ['unknown call path source', s => {s.callPaths[0].sourceIds = ['missing'];}, /unknown call path source/],
  ['duplicate call path source', s => {s.callPaths[0].sourceIds.push(s.callPaths[0].sourceIds[0]);}, /duplicates/],
  ['empty call path boundary', s => {s.callPaths[0].boundary = '';}, /call path boundary/],
  ['unexpected call path URL', s => {s.callPaths[0].url = 'javascript:alert(1)';}, /call path keys/],
  ['early user return', s => {step(s, 'reclaim-chain').changes.userReturned = true;}, /requires completed/],
  ['premature bio completion', s => {step(s, 'write-status').changes.bioCompleted = true;}, /requires reclaimed/],
  ['bio submission before pinning', s => {step(s, 'enter-syscall').changes.blockSubmitted = true;}, /requires pagesMapped/],
  ['injection before LAPIC pending', s => {step(s, 'route-interrupt').changes.irqInjected = true;}, /requires irqPending/],
  ['Guest callback before injection', s => {step(s, 'pending-interrupt').changes.guestNoticed = true;}, /requires irqInjected/],
  ['unknown operation', s => {s.request.operation = 'flush';}, /request operation/],
]) {
  test(`rejects ${label}`, () => {
    const scene = fresh(); mutate(scene);
    assert.throws(() => buildTrace(scene), expected);
  });
}

test('rejects publishing used alongside data and status without preceding ordered stages', () => {
  const scene = fresh();
  step(scene, 'read-data').changes = {dataReady: true, bufferReady: true, statusWritten: true, statusByte: '0x00'};
  assert.throws(() => buildTrace(scene), /dataReady must precede statusWritten/);
});

test('visual relationships distinguish read data from notifications and metadata', () => {
  const trace = buildTrace(source);
  assert.deepEqual(trace.filter(step => step.edge === null).map(step => step.id),
    ['application-ready', 'ready', 'check-kick', 'route-interrupt', 'pending-interrupt']);
  assert.deepEqual(trace.filter(step => step.edge?.kind === 'data').map(step => step.id),
    ['read-data']);
  assert.deepEqual(frame(trace, 'read-data').edge,
    {from: 'storage', to: 'buffer', kind: 'data', label: '写入 4096 B 结果'});
  assert.deepEqual(frame(trace, 'parse-read').edge,
    {from: 'queue', to: 'device', kind: 'metadata', label: '解析命令与映射视图'});
  for (const id of ['write-doorbell', 'signal-ioeventfd', 'dispatch-queue',
    'signal-irqfd', 'guest-notice']) {
    assert.equal(frame(trace, id).edge.kind, 'notification');
  }
  assert(trace.every(step => step.edge === null || step.edge.from !== step.edge.to));
});

test('rejects a flag reverting during forward playback', () => {
  const scene = fresh();
  step(scene, 'publish-available').changes = {prepared: false, freeDescriptors: 8};
  assert.throws(() => buildTrace(scene), /non-monotonic prepared/);
});

test('browser module exports the same pure API without DOM or network access', () => {
  const vm = require('node:vm');
  const code = fs.readFileSync(path.join(__dirname,
    '../virt_report/render/assets/virtio-blk-read-model.js'), 'utf8');
  const context = vm.createContext({});
  vm.runInContext(code, context);
  vm.runInContext(`const sample = ${JSON.stringify(source)};
    if (!VirtioBlkReadModel.validateScenario(sample)) throw new Error('invalid');
    if (!VirtioBlkReadModel.buildTrace(sample).at(-1).state.userReturned) throw new Error('unfinished');
    const write = VirtioBlkReadModel.getScenario(sample, 'write');
    if (VirtioBlkReadModel.buildTrace(write).at(-1).state.usedLength !== 1) throw new Error('write');`, context);
});
