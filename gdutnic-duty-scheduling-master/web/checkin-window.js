/* Check-in window rules shared by the member page and the Node tests.
   Opens `early` minutes before the shift start and closes at the shift end. */
(function (root) {
  function canCheckIn(range, limits, now = Date.now()) {
    return !!range && now >= range.start - limits.early && now < range.end;
  }
  // Labels match the animated attendance control.
  function checkInLabel(range, limits, now = Date.now()) {
    if (!canCheckIn(range, limits, now)) return '未在签到时间内';
    if (now >= range.start + limits.absent) return '确认签到（已记为未到岗）';
    if (now >= range.start + limits.late) return '确认签到（迟到）';
    return '确认签到';
  }
  // `text` is the label span inside the animated button; buttons without one still work.
  function syncCheckInButton(submit, text, range, limits, now = Date.now()) {
    const label = checkInLabel(range, limits, now);
    submit.disabled = !canCheckIn(range, limits, now);
    if (text) text.textContent = label; else submit.textContent = label;
    root.applyNetworkButton?.(submit, 'checkin', text);
  }
  Object.assign(root, {canCheckIn, checkInLabel, syncCheckInButton});
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module === 'object' && module.exports) module.exports = {canCheckIn, checkInLabel};
