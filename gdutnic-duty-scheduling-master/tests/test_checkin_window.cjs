// The check-in window helpers are shared with the page; they need no DOM.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {canCheckIn, checkInLabel} = require('../web/checkin-window.js');

const MIN = 60000;
const start = Date.parse('2026-11-03T13:30:00+08:00');
const range = {start, end: start + 105 * MIN};
const limits = {early: 15 * MIN, late: 10 * MIN, absent: 30 * MIN};
const at = minutes => start + minutes * MIN;

test('check-in stays closed before the early window', () => {
  assert.equal(canCheckIn(range, limits, at(-16)), false);
  assert.equal(checkInLabel(range, limits, at(-16)), '未在签到时间内');
});

test('check-in opens exactly when the early window starts', () => {
  assert.equal(canCheckIn(range, limits, at(-15)), true);
  assert.equal(checkInLabel(range, limits, at(-15)), '确认签到');
});

test('label switches to late then absent as time passes', () => {
  assert.equal(checkInLabel(range, limits, at(0)), '确认签到');
  assert.equal(checkInLabel(range, limits, at(10)), '确认签到（迟到）');
  assert.equal(checkInLabel(range, limits, at(30)), '确认签到（已记为未到岗）');
});

test('check-in closes at the shift end', () => {
  assert.equal(canCheckIn(range, limits, at(104.9)), true);
  assert.equal(canCheckIn(range, limits, at(105)), false);
  assert.equal(checkInLabel(range, limits, at(105)), '未在签到时间内');
});

test('a late early_minutes setting widens the window', () => {
  const wide = {...limits, early: 60 * MIN};
  assert.equal(canCheckIn(range, wide, at(-45)), true);
  assert.equal(canCheckIn(range, limits, at(-45)), false);
});

test('overnight shifts use the computed end after midnight', () => {
  const nightStart = Date.parse('2026-11-03T22:00:00+08:00');
  const night = {start: nightStart, end: nightStart + 4 * 60 * MIN};
  assert.equal(canCheckIn(night, limits, nightStart + 3.9 * 60 * MIN), true);
  assert.equal(canCheckIn(night, limits, nightStart + 4 * 60 * MIN), false);
});
