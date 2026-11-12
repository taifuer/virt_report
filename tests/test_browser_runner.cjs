'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {getPlan} = require('../scripts/run_browser_checks.cjs');
const {browserOptions} = require('../scripts/browser_runtime.cjs');

test('default browser checks are bounded smoke journeys, not screenshot sweeps', () => {
  assert.deepEqual(getPlan(), {mode: 'smoke', suites: ['smoke'], timeoutMs: 180000});
  assert.deepEqual(getPlan(['--smoke']), getPlan());
});

test('review and full audits preserve the existing suites as explicit options', () => {
  const review = getPlan(['--review']), full = getPlan(['--full']);
  assert.equal(review.suites.length, 11);
  assert(review.suites.includes('execution_demos'));
  assert.deepEqual(full.suites, [...review.suites, 'walkthrough_global']);
  assert(!review.suites.includes('smoke'));
  review.suites.pop();
  assert.equal(getPlan(['--review']).suites.length, 11, 'plans do not mutate the catalogue');
});

test('invalid or conflicting modes fail rather than silently running a slow audit', () => {
  for (const args of [['--ful'], ['--full', '--review'], ['--smoke', '--smoke']]) {
    assert.throws(() => getPlan(args), /Usage:/);
  }
});

test('all browser modes use the same explicitly configurable launcher', () => {
  for (const suite of ['smoke', ...getPlan(['--full']).suites]) {
    const source = fs.readFileSync(path.join(__dirname, `../scripts/check_${suite}_browser.cjs`), 'utf8');
    assert(source.includes("require('./browser_runtime.cjs')"), suite);
    assert(!source.includes('chromium.launch('), suite);
  }
});

test('CI reuses installed Chrome and local checks retain the bundled browser', () => {
  const options = {headless: true, args: ['--no-sandbox']};
  assert.deepEqual(browserOptions(options, {}), options);
  assert.deepEqual(browserOptions(options, {PLAYWRIGHT_CHANNEL: 'chrome'}), {...options, channel: 'chrome'});
  assert.equal(options.channel, undefined, 'caller options are not mutated');
});
