function weekBoardRows(days) {
  const shifts = days.flatMap(day => day.shifts);
  // The special flag records creation through the special-schedule endpoint.
  // A missing weekly link is not evidence that a shift is special.
  const isSpecial = s => s.special === true || Number(s.special) === 1;
  const regular = shifts.filter(s => !isSpecial(s));
  const explicitSlot = s => {
    if (Number.isInteger(s.weekly_slot) && s.weekly_slot >= 0 && s.weekly_slot < 4) return s.weekly_slot;
    const match = /^第\s*([1-4一二三四])\s*班$/.exec(String(s.note || '').trim());
    return match ? ('一二三四'.includes(match[1]) ? '一二三四'.indexOf(match[1]) : Number(match[1]) - 1) : null;
  };
  const timeKey = s => `${s.start}|${s.end}`;
  const timeSlots = new Map();
  regular.forEach(s => { const slot = explicitSlot(s); if (slot !== null) timeSlots.set(timeKey(s), slot); });
  const unmatched = [...new Set(regular.filter(s => !timeSlots.has(timeKey(s))).map(timeKey))].sort();
  const used = new Set(timeSlots.values());
  unmatched.forEach(time => {
    const slot = [0, 1, 2, 3].find(i => !used.has(i)) ?? 3;
    timeSlots.set(time, slot); used.add(slot);
  });
  const byTime = (a, b) => a.start.localeCompare(b.start) || a.end.localeCompare(b.end) || a.id - b.id;
  const rows = Array.from({length: 4}, (_, slot) => ({
    label: `第 ${slot + 1} 班`,
    cells: days.map(day => day.shifts.filter(s => !isSpecial(s)
      && (explicitSlot(s) ?? timeSlots.get(timeKey(s))) === slot).slice().sort(byTime)),
  }));
  if (shifts.some(isSpecial)) {
    rows.push({
      label: '特殊排班', special: true,
      cells: days.map(day => day.shifts.filter(isSpecial).slice().sort(byTime)),
    });
  }
  return rows;
}
