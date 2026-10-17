const assert = require('node:assert/strict');
const {test} = require('node:test');
const {buildNotificationRace} = require('../virt_report/render/assets/device-tx.js');

test('recheck consumes an early request without a kick', () => {
  const frames = buildNotificationRace('recheck');
  const end = frames.at(-1);
  assert.equal(end.avail, 8);
  assert.equal(end.consumed, 8);
  assert.equal(end.kickSent, false);
  assert.ok(frames.every(frame => !frame.waiting));
});
test('omitting recheck leaves the published request pending', () => {
  const end = buildNotificationRace('broken').at(-1);
  assert.equal(end.avail, 8);
  assert.equal(end.consumed, 7);
  assert.equal(end.notifications, true);
  assert.equal(end.kickSent, false);
  assert.equal(end.waiting, true);
});
test('a late request is accompanied by a kick', () => {
  const frames = buildNotificationRace('late');
  const end = frames.at(-1);
  assert.equal(end.consumed, end.avail);
  assert.equal(end.kickSent, true);
  assert.ok(frames.find(frame => frame.title === '复查时队列仍空').notifications);
});
test('traces have independent frames and reject unknown scenarios', () => {
  const first = buildNotificationRace();
  first[0].avail = 99;
  assert.equal(first[1].avail, 8);
  assert.equal(buildNotificationRace()[0].avail, 7);
  assert.throws(() => buildNotificationRace('other'), RangeError);
});
