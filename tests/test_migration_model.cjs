/* Run with: node --test tests/test_migration_model.cjs */
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/mechanisms.js');

function run(parameters) {
  const states = [model.createState(parameters)];
  while (!model.finished(states.at(-1))) {
    assert.ok(states.length <= model.MAX_ROUNDS + 6, 'observation must remain bounded');
    states.push(model.advance(states.at(-1)));
  }
  return states;
}

function near(actual, expected) { assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} differs from ${expected}`); }

test('default 8 GiB example uses explicit MiB and time units with four running rounds', () => {
  const states = run();
  assert.deepEqual(states.map(state => state.phase), ['ready', 'precopy', 'precopy', 'precopy', 'precopy', 'paused', 'final_ram', 'state_transfer', 'complete']);
  near(states[0].parameters.memoryMiB, 8192);
  near(states[1].lastDurationSeconds, 8.192);
  near(states[1].remainingMiB, 2097.152);
  near(states[2].lastDurationSeconds, 2.097152);
  near(states[2].remainingMiB, 536.870912);
  near(states[3].lastDurationSeconds, 0.536870912);
  near(states[3].remainingMiB, 137.438953472);
  near(states[4].remainingMiB, 35.184372088832);
  near(states[5].thresholdMiB, 100);
  const complete = states.at(-1);
  near(complete.transferredMiB, 10998.646237560832);
  near(complete.elapsedSeconds, 11.03864623756083);
  near(complete.downtimeMs, 75.184372088832);
});

test('final RAM, execution state and destination restore are separate stopped stages', () => {
  const states = run();
  const ram = states.find(state => state.phase === 'final_ram');
  const execution = states.find(state => state.phase === 'state_transfer');
  near(ram.elapsedSeconds, 10.99864623756083);
  near(execution.elapsedSeconds, 11.01864623756083);
  near(ram.remainingMiB, 0);
  assert.equal(ram.destinationStateReady, false);
  assert.equal(execution.destinationStateReady, true);
  for (const state of [ram, execution]) {
    assert.equal(state.sourceRunning, false);
    assert.equal(state.destinationRunning, false);
    near(state.transferredMiB, 10998.646237560832);
    near(state.stopRemainingMiB, 35.184372088832);
  }
});

test('visual memory cells aggregate equal capacity and do not invent page counts', () => {
  for (const state of run()) {
    const regions = model.memoryRegions(state);
    assert.equal(regions.length, 12);
    assert.deepEqual(regions.map(region => region.label), [...'ABCDEFGHIJKL']);
    assert.ok(regions.every(region => region.dirtyFraction >= 0 && region.dirtyFraction <= 1));
    near(regions.reduce((sum, region) => sum + region.dirtyFraction * state.parameters.memoryMiB / 12, 0), state.remainingMiB);
  }
});

test('stop threshold excludes illustrative CPU/device/restore overhead', () => {
  const states = run();
  const paused = states.find(state => state.phase === 'paused');
  near(paused.thresholdMiB, paused.parameters.bandwidthMiBps * paused.parameters.downtimeMs / 1000);
  const timing = model.estimateDowntime(paused);
  near(timing.ramMs, 35.184372088832);
  near(timing.stateMs, 20);
  near(timing.restoreMs, 20);
  near(timing.totalMs, 75.184372088832);
  assert.deepEqual(model.estimateDowntime(states.at(-1)), model.estimateDowntime(paused));
  assert.equal(paused.sourceRunning, false);
  assert.equal(paused.destinationRunning, false);
  assert.equal(paused.downtimeMs, null);
});

test('illustrative state overhead can exceed the downtime target', () => {
  const complete = run({memoryMiB: 1024, bandwidthMiBps: 1024}).at(-1);
  near(complete.downtimeMs, 102.5);
  assert.ok(complete.downtimeMs > complete.parameters.downtimeMs, 'target is not a hard downtime guarantee');
});

test('zero dirtying still performs the initial copy before stopping', () => {
  const states = run({dirtyMiBps: 0, downtimeMs: 10000});
  assert.equal(states[1].rounds, 1);
  near(states[1].transferredMiB, 8192);
  near(states[1].remainingMiB, 0);
  near(states.at(-1).downtimeMs, 40);
  near(states.at(-1).elapsedSeconds, 8.232);
});

test('4, 8 and 16 GiB memory selections change real transfer quantities', () => {
  const finals = [4096, 8192, 16384].map(memoryMiB => run({memoryMiB}).at(-1));
  for (const state of finals) assert.equal(state.phase, 'complete');
  assert.ok(finals[0].transferredMiB < finals[1].transferredMiB);
  assert.ok(finals[1].transferredMiB < finals[2].transferredMiB);
  assert.ok(finals[0].elapsedSeconds < finals[1].elapsedSeconds);
  assert.ok(finals[1].elapsedSeconds < finals[2].elapsedSeconds);
});

test('100 versus 1000 MiB/s changes convergence without silently reducing dirty rate', () => {
  const slow = run({bandwidthMiBps: 100});
  const fast = run({bandwidthMiBps: 1000});
  assert.equal(slow.at(-1).phase, 'precopy');
  assert.equal(slow.at(-1).endReason, 'stable');
  assert.equal(slow.at(-1).sourceRunning, true);
  assert.equal(fast.at(-1).phase, 'complete');
  near(slow[1].lastDurationSeconds, 81.92);
  near(fast[1].lastDurationSeconds, 8.192);
  assert.equal(slow.at(-1).parameters.dirtyMiBps, 256);
  assert.equal(fast.at(-1).parameters.dirtyMiBps, 256);
});

test('slow convergence takes more rounds while reducing dirtying reduces transfer work', () => {
  const easy = run(model.PRESETS.easy).at(-1);
  const slow = run(model.PRESETS.slow).at(-1);
  assert.equal(slow.phase, 'complete');
  assert.ok(slow.rounds > easy.rounds);
  assert.ok(slow.transferredMiB > easy.transferredMiB);
  assert.ok(slow.elapsedSeconds > easy.elapsedSeconds);
  const faster = run({...model.PRESETS.slow, bandwidthMiBps: 2048}).at(-1);
  assert.equal(faster.phase, 'complete');
  assert.ok(faster.rounds < slow.rounds);
  assert.ok(faster.elapsedSeconds < slow.elapsedSeconds);
});

test('relaxing the target trades fewer rounds for more work while stopped', () => {
  const tight = run().at(-1);
  const relaxed = run({downtimeMs: 300}).at(-1);
  assert.ok(relaxed.rounds < tight.rounds);
  assert.ok(relaxed.transferredMiB < tight.transferredMiB);
  assert.ok(relaxed.downtimeMs > tight.downtimeMs);
  assert.ok(relaxed.stopRemainingMiB > tight.stopRemainingMiB);
});

test('equal or faster dirtying saturates at memory size and does not fake completion', () => {
  for (const dirtyMiBps of [1000, 1200, 8192]) {
    const states = run({dirtyMiBps});
    const last = states.at(-1);
    assert.equal(last.phase, 'precopy');
    assert.equal(last.observationEnded, true);
    assert.equal(last.endReason, 'stable');
    assert.equal(last.rounds, model.STABLE_ROUNDS);
    assert.equal(last.sourceRunning, true);
    assert.equal(last.destinationRunning, false);
    assert.equal(last.downtimeMs, null);
    assert.ok(states.every(state => state.remainingMiB <= state.parameters.memoryMiB));
    assert.equal(model.advance(last), last);
  }
});

test('very slow convergence remains bounded without automatically stopping the VM', () => {
  const last = run({dirtyMiBps: 999}).at(-1);
  assert.equal(last.rounds, model.MAX_ROUNDS);
  assert.equal(last.endReason, 'limit');
  assert.equal(last.sourceRunning, true);
  assert.equal(last.destinationRunning, false);
  assert.equal(last.stopRemainingMiB, null);
});

test('all transitions preserve immutable replay and exclusive execution', () => {
  for (const parameters of Object.values(model.PRESETS)) {
    const states = run(parameters);
    assert.deepEqual(states, run(parameters));
    for (const state of states) {
      const before = JSON.stringify(state);
      model.advance(state);
      assert.equal(JSON.stringify(state), before);
      assert.equal(state.sourceRunning && state.destinationRunning, false);
      assert.ok(Number.isFinite(state.elapsedSeconds));
      assert.ok(state.transferredMiB >= 0);
      assert.ok(state.remainingMiB >= 0);
      if (state.phase === 'complete') assert.equal(state.remainingMiB, 0);
    }
  }
});

test('invalid and unbounded parameter values are rejected', () => {
  for (const bad of [null, 2, [], {memoryMiB: 0}, {bandwidthMiBps: 0}, {dirtyMiBps: -1}, {downtimeMs: 0}, {memoryMiB: Infinity}, {dirtyMiBps: NaN}, {downtimeMs: '100'}, {memoryMiB: 2 ** 32}]) {
    assert.throws(() => model.createState(bad), RangeError);
  }
  assert.deepEqual(model.validateParameters(), model.DEFAULT_PARAMETERS);
});

test('extreme valid parameters preserve finite units and honest stop criteria', () => {
  const generous = run({memoryMiB: 64, bandwidthMiBps: 1048576, dirtyMiBps: 1048576, downtimeMs: 10000});
  assert.equal(generous[1].remainingMiB, 64);
  assert.equal(generous[1].thresholdMiB, 10485760);
  assert.equal(generous.at(-1).phase, 'complete');
  assert.equal(generous.at(-1).rounds, 1);
  assert.equal(generous.at(-1).transferredMiB, 128);
  assert.ok(generous.at(-1).downtimeMs < 41);
  const saturated = run({memoryMiB: 1048576, bandwidthMiBps: 1, dirtyMiBps: 1048576, downtimeMs: 1});
  assert.equal(saturated.at(-1).endReason, 'stable');
  assert.equal(saturated.at(-1).downtimeMs, null);
  for (const state of [...generous, ...saturated]) {
    for (const key of ['remainingMiB', 'elapsedSeconds', 'transferredMiB', 'thresholdMiB']) assert.ok(Number.isFinite(state[key]));
    assert.ok(state.remainingMiB <= state.parameters.memoryMiB);
    assert.ok(state.remainingMiB >= 0);
  }
});
