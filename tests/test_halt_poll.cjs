'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../virt_report/render/assets/halt-poll.js');

test('a short wait is handled by polling without the selected scheduling delay', () => {
  const result = model.simulate();
  assert.equal(result.outcome, 'poll');
  assert.equal(result.slept, false);
  assert.equal(result.returnUs, 30);
  assert.equal(result.eventToReturnUs, 0);
  assert.deepEqual(result.segments, [{kind: 'poll', start: 0, end: 30}]);
  assert.equal(result.adaptation.nextUs, 50);
});

test('pending at the first check returns immediately even with a zero budget', () => {
  for (const pollUs of [0, 50, 200]) {
    const result = model.simulate({eventUs: 0, pollUs});
    assert.equal(result.outcome, 'pending');
    assert.equal(result.returnUs, 0);
    assert.equal(result.activeUs, 0);
    assert.equal(result.segments.length, 0);
  }
});

test('events at either preparation boundary are retained for the final recheck', () => {
  for (const eventUs of [50, 55, 60]) {
    const result = model.simulate({eventUs, pollUs: 50, dispatchUs: 200});
    assert.equal(result.outcome, 'recheck');
    assert.equal(result.slept, false);
    assert.equal(result.returnUs, 60);
    assert.equal(result.eventToReturnUs, 60 - eventUs);
    assert.equal(result.activeUs, 60);
    assert.ok(!result.segments.some(s => ['blocked', 'dispatch'].includes(s.kind)));
    assert.equal(result.adaptation.nextUs, 100);
  }
});

test('late events wake sleeping tasks; wake and actual execution are separate', () => {
  const result = model.simulate({eventUs: 120, pollUs: 50, dispatchUs: 40});
  assert.equal(result.outcome, 'sleep');
  assert.equal(result.slept, true);
  assert.equal(result.returnUs, 160);
  assert.equal(result.eventToReturnUs, 40);
  assert.deepEqual(result.segments, [
    {kind: 'poll', start: 0, end: 50}, {kind: 'prepare', start: 50, end: 60},
    {kind: 'blocked', start: 60, end: 120}, {kind: 'dispatch', start: 120, end: 160}
  ]);
  assert.equal(result.adaptation.nextUs, 100);
});

test('a zero budget skips polling while preserving the wait preparation and race check', () => {
  const early = model.simulate({eventUs: 8, pollUs: 0});
  assert.equal(early.outcome, 'recheck');
  assert.equal(early.pollingUs, 0);
  assert.equal(early.returnUs, 10);
  assert.equal(early.adaptation.nextUs, 10);
  const late = model.simulate({eventUs: 11, pollUs: 0, dispatchUs: 0});
  assert.equal(late.outcome, 'sleep');
  assert.equal(late.returnUs, 11);
  assert.ok(!late.segments.some(s => s.kind === 'dispatch'));
});

test('adaptation uses total halt time, including the chosen dispatch delay', () => {
  assert.equal(model.simulate({eventUs: 170, dispatchUs: 0}).adaptation.action, '增长');
  assert.equal(model.simulate({eventUs: 170, dispatchUs: 30}).adaptation.action, '保持');
  assert.equal(model.simulate({eventUs: 170, dispatchUs: 31}).adaptation.action, '缩小');
});

test('adaptation handles zero, shrink floor, equality, growth and next-entry cap', () => {
  assert.deepEqual(model.adapt(0, 20), {currentUs: 0, storedUs: 10, nextUs: 10, action: '增长'});
  assert.equal(model.adapt(50, 50).action, '保持');
  assert.equal(model.adapt(50, 200).action, '保持');
  assert.equal(model.adapt(50, 201).nextUs, 25);
  assert.equal(model.adapt(25, 201).nextUs, 12.5);
  assert.equal(model.adapt(20.001, 201).nextUs, 10);
  assert.equal(model.adapt(19, 201).nextUs, 0);
  assert.equal(model.adapt(20, 201).nextUs, 10);
  assert.equal(model.adapt(0, 201).nextUs, 0);
  assert.deepEqual(model.adapt(150, 180), {currentUs: 150, storedUs: 300, nextUs: 200, action: '增长'});
  assert.deepEqual(model.adapt(400, 50), {currentUs: 200, storedUs: 200, nextUs: 200, action: '保持'});
  assert.equal(model.adapt(50, 100, 0).nextUs, 0);
});

test('repeated short waits grow from zero to a window that contains the event', () => {
  let pollUs = 0;
  const seen = [];
  for (let i = 0; i < 7; i++) {
    const result = model.simulate({eventUs: 70, dispatchUs: 20, pollUs});
    seen.push(pollUs);
    pollUs = result.adaptation.nextUs;
  }
  assert.deepEqual(seen, [0, 10, 20, 40, 80, 80, 80]);
});

test('fractional microsecond budgets retain nanosecond precision across updates', () => {
  const result = model.simulate({pollUs: 10.002, eventUs: 15});
  assert.equal(result.returnUs, 20.002);
  assert.equal(result.eventToReturnUs, 5.002);
  assert.equal(result.activeUs, 20.002);
  assert.equal(result.adaptation.nextUs, 20.004);
  const {get, controller} = fixture();
  get('event').value = '500'; get('poll').value = '25'; controller.refresh();
  get('next').fire('click');
  assert.equal(controller.state().config.pollUs, 12.5);
  assert.equal(get('error').hidden, true);
});

test('all timeline intervals are ordered, positive and account for elapsed time', () => {
  for (const eventUs of [0, 1, 10, 49, 50, 55, 60, 61, 199, 200, 201, 1000]) {
    for (const pollUs of [0, 1, 50, 150, 200]) for (const dispatchUs of [0, 40, 200]) {
      const result = model.simulate({eventUs, pollUs, dispatchUs});
      let end = 0;
      for (const segment of result.segments) {
        assert.equal(segment.start, end);
        assert.ok(segment.end > segment.start);
        end = segment.end;
      }
      assert.equal(end, result.returnUs);
      assert.ok(result.returnUs >= eventUs);
      assert.ok(result.events.every((e, i, list) => !i || e.at >= list[i - 1].at));
      assert.ok(result.adaptation.nextUs >= 0 && result.adaptation.nextUs <= model.MAX_POLL_US);
      assert.ok(result.activeUs <= result.returnUs);
    }
  }
});

test('validation rejects partial parses, coercion traps, fractions and invalid ranges', () => {
  assert.deepEqual(model.normalize({eventUs: ' 55 ', pollUs: '0', dispatchUs: '40'}), {eventUs: 55, pollUs: 0, dispatchUs: 40});
  for (const value of ['', ' ', '10us', '-1', '1.5', '1e2', 'Infinity', NaN, Infinity, null, true, [], {}]) {
    assert.throws(() => model.simulate({eventUs: value}), RangeError);
  }
  assert.throws(() => model.simulate({pollUs: 201}), RangeError);
  assert.throws(() => model.simulate({pollUs: '20.0001'}), RangeError);
  assert.equal(model.simulate({pollUs: '12.5'}).config.pollUs, 12.5);
  assert.throws(() => model.simulate({dispatchUs: 201}), RangeError);
  assert.throws(() => model.simulate({eventUs: 1001}), RangeError);
  assert.throws(() => model.simulate(null), TypeError);
  assert.throws(() => model.adapt(0, -1), RangeError);
  assert.throws(() => model.adapt(1001, 0), RangeError);
});

class Element {
  constructor(tag = '') { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.listeners = {}; this.attributes = {}; this.hidden = true; this.value = ''; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  fire(name, event = {}) { this.listeners[name]?.(event); }
}

function fixture() {
  const nodes = new Map(), lab = new Element();
  const get = name => { if (!nodes.has(name)) nodes.set(name, new Element()); return nodes.get(name); };
  lab.querySelector = selector => get(selector.match(/^\[data-halt-poll-(.*)\]$/)[1]);
  for (const [name, value] of Object.entries({event: '30', poll: '50', dispatch: '40'})) get(name).value = value;
  const controller = model.mount(lab, {document: {createElement: tag => new Element(tag)}});
  return {get, controller};
}

test('mount exposes functional controls, accessible timeline and full event descriptions', () => {
  const {get, controller} = fixture();
  assert.equal(get('controls').hidden, false);
  assert.equal(get('fallback').hidden, true);
  assert.equal(get('scene').hidden, false);
  assert.match(get('status').textContent, /没有睡眠/);
  assert.match(get('timeline').attributes['aria-label'], /0—30 µs/);
  assert.equal(get('events').children.length, controller.state().events.length);
  assert.equal(get('next').disabled, true);
  get('after').fire('click');
  assert.equal(controller.state().returnUs, 160);
  assert.match(get('status').textContent, /睡眠后被唤醒/);
  assert.equal(get('next').disabled, false);
  get('next').fire('click');
  assert.equal(get('poll').value, '100');
});

test('race preset, edits and reset update the same model without automatic progression', () => {
  const {get, controller} = fixture();
  get('race').fire('click');
  assert.equal(controller.state().outcome, 'recheck');
  assert.equal(controller.state().returnUs, 60);
  get('event').value = '61'; get('event').fire('input');
  assert.equal(controller.state().outcome, 'sleep');
  get('dispatch').value = '0'; get('dispatch').fire('input');
  assert.equal(controller.state().returnUs, 61);
  get('reset').fire('click');
  assert.deepEqual(controller.state().config, model.DEFAULTS);
});

test('bad input hides stale output, attributes invalid field and clears it on correction', () => {
  const {get, controller} = fixture();
  let prevented = false;
  get('event').value = 'wrong';
  get('controls').fire('submit', {preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(controller.state(), null);
  assert.equal(get('scene').hidden, true);
  assert.equal(get('error').hidden, false);
  assert.equal(get('event').attributes['aria-invalid'], 'true');
  get('event').value = '0'; get('event').fire('input');
  assert.equal(controller.state().outcome, 'pending');
  assert.equal(get('scene').hidden, false);
  assert.equal(get('error').hidden, true);
  assert.equal(get('event').attributes['aria-invalid'], undefined);
  assert.equal(get('track').children[0].style.left, '0%');
});
