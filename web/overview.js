// Admin overview: monthly attendance stats, current-duty timeline, and this week's board.
const WEEK_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const STATUS_TEXT = {normal:'正常', late:'迟到', absent:'旷班', pending:'待签到', unconfirmed:'待确认'};
let overviewData = null, overviewToken = 0, detailShiftId = null;
// Anchor for the week board: any day inside the requested week, or null for this week.
let overviewWeek = null;

// 概览固定显示本月；月份选择已从页面移除。
const currentMonth = () => today().slice(0, 7);
const shiftTime = s => `${s.start} — ${s.end < s.start ? '次日 ' : ''}${s.end}`;
const memberLine = m => `${m.name} · ${STATUS_TEXT[m.status] || '待签到'}`;

function overviewCard(s) {
  const late = s.members.filter(m => m.status === 'late');
  const absent = s.members.filter(m => m.status === 'absent');
  const tone = absent.length ? 'danger' : late.length ? 'warn' : s.phase === 'current' ? 'current'
    : s.phase === 'future' ? 'future' : 'ok';
  const card = el('button', `week-card ${tone}`);
  card.type = 'button';
  card.dataset.shiftId = String(s.id);
  card.append(el('strong', 'week-card-time', shiftTime(s)));
  card.append(el('span', 'week-card-count', `${s.members.length} 人`));
  // Reserve room for three names so every card keeps the same height; extra names are collapsed.
  const flagged = [...absent.map(m => ({m, status:'absent'})), ...late.map(m => ({m, status:'late'}))];
  for (let i = 0; i < 3; i++) {
    const item = flagged[i];
    card.append(el('span', `week-card-name${item ? ' ' + item.status : ' blank'}`,
      item ? `${item.m.name} · ${STATUS_TEXT[item.m.status]}` : ' '));
  }
  if (flagged.length > 3) card.append(el('span', 'week-card-more', `另有 ${flagged.length - 3} 人`));
  card.onclick = () => openDetail(s);
  return card;
}

function renderWeek(week, today) {
  // The server decides both bounds, so the arrows only enable where a week exists.
  $('weekRange').textContent = `${week.start} 至 ${week.end}`;
  const isCurrent = week.start === week.current;
  // The heading would be wrong while browsing another week.
  $('weekTitle').textContent = isCurrent ? '本周排班' : '周排班';
  $('weekToday').disabled = isCurrent;
  // Deleting an empty week would be a no-op, so the button follows the shift count.
  $('weekClear').disabled = !week.shifts;
  $('weekClear').title = week.shifts
    ? `删除 ${week.start} 至 ${week.end} 的 ${week.shifts} 个班次`
    : '这一周没有排班';
  const prev = $('weekPrev'), next = $('weekNext');
  prev.disabled = !week.previous;
  next.disabled = !week.next;
  prev.title = week.previous ? `上一周（${week.previous} 起）` : '已到最早一周';
  next.title = week.next ? `下一周（${week.next} 起）` : '已到最晚一周';
  const board = $('weekBoard');
  board.specialGapObserver?.disconnect();
  board.replaceChildren();
  // A week with no shifts says so instead of rendering an empty seven-column grid.
  if (!week.days.some(day => day.shifts.length)) {
    board.append(el('p', 'hint week-empty',
      isCurrent ? '本周暂无排班。' : `${week.start} 至 ${week.end} 没有排班。`));
    return;
  }
  const rows = weekBoardRows(week.days);
  const boardHead = el('div', 'week-row week-head-row');
  boardHead.style.gridRow = '1';
  boardHead.append(el('div', 'week-day-head', '星期'));
  for (const day of week.days) {
    boardHead.append(el('div', 'week-day-head', `${WEEK_NAMES[day.weekday]} ${day.day.slice(5)}${day.day === today ? ' · 今天' : ''}`));
  }
  board.append(boardHead);
  for (let index = 0; index < rows.length; index++) {
    const row = el('div', 'week-row' + (rows[index].special ? ' week-special-row' : ''));
    row.style.gridRow = String(index + 2);
    row.append(el('div', 'week-slot-label', rows[index].label));
    for (const [dayIndex, day] of week.days.entries()) {
      const cell = el('div', 'week-cell' + (day.day === today ? ' today' : ''));
      rows[index].cells[dayIndex].forEach(shift => cell.append(overviewCard(shift)));
      row.append(cell);
    }
    board.append(row);
  }
  if (rows.some(row => row.special)) {
    const regularRows = Array.from(board.querySelectorAll('.week-row:not(.week-head-row):not(.week-special-row)'));
    const updateUnit = () => board.style.setProperty('--week-unit',
      `${Math.max(...regularRows.map(row => row.getBoundingClientRect().height))}px`);
    updateUnit();
    if (typeof ResizeObserver !== 'undefined') {
      board.specialGapObserver = new ResizeObserver(updateUnit);
      regularRows.forEach(row => board.specialGapObserver.observe(row));
    }
  }
}

function openDetail(s) {
  detailShiftId = s.id;
  $('detailTitle').textContent = `${s.day} ${shiftTime(s)}`;
  $('detailMeta').textContent = `${s.note || '值班班次'} · ${s.members.length} 人`;
  const list = $('detailMembers');
  list.replaceChildren();
  for (const m of s.members) {
    // Only a holder of 更改考勤状态 gets a button; everyone else sees a plain row.
    const editable = !!window.canEditAttendance;
    const row = el(editable ? 'button' : 'div',
      `detail-member ${editable ? 'clickable ' : ''}${m.status || 'pending'}`);
    if (editable) {
      row.type = 'button';
      row.title = '点击更改考勤状态';
      row.onclick = () => editAttendance(s, m);
    }
    row.append(el('span', 'detail-name', m.name), el('span', 'detail-status', STATUS_TEXT[m.status] || '待签到'));
    row.append(el('span', 'detail-time', m.checked_at
      ? new Date(m.checked_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'})
      : '未签到'));
    list.append(row);
  }
  if (!$('detailDialog').open) $('detailDialog').showModal();
}

// Manual fixes take precedence over the automatic late/absent calculation.
function editAttendance(shift, member) {
  if (!window.canEditAttendance) return;
  openStatusEditor({
    title: '更改考勤状态',
    meta: `${member.name} · ${shift.day} ${shiftTime(shift)}`,
    kind: 'attendance',
    current: member.status === 'pending' ? 'normal' : member.status,
    extraNote: member.checked_at
      ? '已签到：手动状态将覆盖系统根据签到时间计算的结果。'
      : '未签到：可手动改判为准时、迟到或旷班。',
    onSave: async status => {
      await api('/api/admin/overview/attendance', 'PUT',
        {shift_id: shift.id, name: member.name, status});
      await loadOverview();
      // Reopen the refreshed shift so the dialog shows the new status. The board may
      // be showing any week, so look there as well as in today's timeline.
      const fresh = overviewData && (overviewData.timeline.find(item => item.id === shift.id)
        || overviewData.week.days.flatMap(day => day.shifts).find(item => item.id === shift.id));
      if (fresh) openDetail(fresh);
      $('status').textContent = `已将「${member.name}」的考勤状态改为${STATUS_TEXT[status]}。`;
    },
  });
}

async function loadOverview() {
  const month = currentMonth();
  const token = ++overviewToken;
  $('status').textContent = '正在加载概览…';
  try {
    const query = '/api/admin/overview?month=' + encodeURIComponent(month)
      + (overviewWeek ? '&week=' + encodeURIComponent(overviewWeek) : '');
    const data = await api(query);
    if (token !== overviewToken) return;
    overviewData = data;
    // Track the week the server actually rendered; it is authoritative.
    overviewWeek = data.week.start === data.week.current ? null : data.week.start;
    renderOverview();
    await loadAnomaly(anomalyWeeks);
    if (token !== overviewToken) return;
    $('status').textContent = '';
  } catch (err) {
    if (token === overviewToken) $('status').textContent = err.message;
  }
}

// Step one week back or forward; the buttons are disabled at the bounds.
async function shiftWeek(direction) {
  const week = overviewData?.week;
  const target = direction < 0 ? week?.previous : week?.next;
  if (!target) return;
  overviewWeek = target;
  await loadOverview();
}

// Delete the whole visible week, archiving its check-ins first.
function clearVisibleWeek() {
  const week = overviewData?.week;
  if (!week || !week.shifts) return;
  const phrase = '删除本周排班';
  openRangeClear({
    title: '确认删除整周排班',
    phrase,
    body: `将删除 ${week.start} 至 ${week.end} 的 ${week.shifts} 个班次、`
      + `${week.people} 条排班人员记录；已签到及已确认的考勤结果会归档到出勤历史，`
      + `仍可在统计中查到。每周固定排班表和账号不受影响。`,
    submit: () => api(`/api/admin/schedules/week?start=${encodeURIComponent(week.start)}`, 'DELETE'),
    onDone: async () => {
      await loadOverview();
      $('status').textContent = `已删除 ${week.start} 至 ${week.end} 的整周排班。`;
    },
  });
}

function renderOverview(changedIds = null) {
  if (!overviewData) return;
  const stats = overviewData.stats;
  $('attendanceRate').textContent = stats.rate === null ? '—' : `${stats.rate}%`;
  $('lateCount').textContent = stats.late;
  $('absentCount').textContent = stats.absent;
  items = overviewData.timeline;
  window.updateShiftTimeline(items);
  if (!changedIds) renderWeek(overviewData.week, overviewData.today);
  else {
    for (const shift of overviewData.week.days.flatMap(day => day.shifts)) {
      if (!changedIds.has(shift.id)) continue;
      const card = $('weekBoard').querySelector(`[data-shift-id="${shift.id}"]`);
      if (card) card.replaceWith(overviewCard(shift));
    }
  }
}

// Sidebar visibility follows the same page ids checked by the API.
function applyPermissions(granted) {
  const held = new Set(Array.isArray(granted) ? granted : []);
  // An entry is visible when the account holds any of its permissions. 其他设置 has no
  // page leaf of its own: either card's permission opens the page.
  const needs = {dailyTab: ['overview'], weeklyTab: ['weekly'], specialTab: ['special'],
    swapTab: ['swap'], fieldTab: ['field'], membersTab: ['members'],
    permissionGroupsTab: ['permission_groups'], pushTab: ['push'], logsTab: ['logs'], dataTab: ['data'],
    settingsTab: ['rules', 'clock', 'smtp', 'checkin_manage', 'sso']};
  let firstVisible = null;
  for (const [id, permissions] of Object.entries(needs)) {
    const node = $(id);
    if (!node) continue;
    node.hidden = !permissions.some(permission => held.has(permission));
    if (!node.hidden && !firstVisible) firstVisible = id;
  }
  const active = document.querySelector('.ga-nav-item.active');
  if ((!active || active.hidden) && firstVisible) $(firstVisible).click();
}

function initOverview() {
  // 月份固定为本月，无需选择控件。
  $('refresh').onclick = loadOverview;
  $('weekPrev').onclick = () => shiftWeek(-1);
  $('weekNext').onclick = () => shiftWeek(1);
  $('weekToday').onclick = () => { overviewWeek = null; loadOverview(); };
  $('weekClear').onclick = clearVisibleWeek;
  $('closeDetail').onclick = () => $('detailDialog').close();
  window.loadOverview = loadOverview;
  window.applyPermissions = applyPermissions;
  window.renderOverview = renderOverview;
}
if (document.body.dataset.admin === 'true') initOverview();

// 异常统计：近 2 周 / 近 4 周的迟到与旷班横向排行。
let anomalyWeeks = 2;
let anomalyToken = 0;

function renderAnomalyChart(container, items, totalLabel) {
  container.replaceChildren();
  if (!items.length) {
    container.append(el('p', 'anomaly-empty', '统计周期内没有记录'));
    return 0;
  }
  const max = Math.max(...items.map(item => item.count), 1);
  for (const item of items) {
    const row = el('div', 'anomaly-row');
    const name = el('span', 'anomaly-name', item.name);
    name.title = item.name;
    const track = el('div', 'anomaly-track');
    const fill = el('div', 'anomaly-fill');
    fill.style.width = `${Math.max(4, Math.round(item.count / max * 100))}%`;
    track.append(fill);
    row.append(name, track, el('span', 'anomaly-count', String(item.count)));
    container.append(row);
  }
  return items.reduce((sum, item) => sum + item.count, 0);
}

async function loadAnomaly(weeks = anomalyWeeks) {
  const token = ++anomalyToken;
  anomalyWeeks = weeks;
  $('anomaly2').classList.toggle('active', weeks === 2);
  $('anomaly4').classList.toggle('active', weeks === 4);
  try {
    const data = await api('/api/admin/anomalies?weeks=' + weeks);
    if (token !== anomalyToken) return;
    const lateTotal = renderAnomalyChart($('lateChart'), data.late);
    const absentTotal = renderAnomalyChart($('absentChart'), data.absent);
    $('lateTotal').textContent = `共 ${lateTotal} 次`;
    $('absentTotal').textContent = `共 ${absentTotal} 次`;
    $('anomalyRange').textContent = `统计范围：${data.start} 至 ${data.end}（近 ${data.weeks} 周）`;
  } catch (err) {
    if (token === anomalyToken) $('anomalyRange').textContent = err.message;
  }
}

$('anomaly2').onclick = () => loadAnomaly(2);
$('anomaly4').onclick = () => loadAnomaly(4);

// Measure natural card widths even while hidden, so resizing restores the group.
function updateStatsVisibility(group) {
  const cards = Array.from(group.children);
  const gap = parseFloat(getComputedStyle(group).columnGap) || 0;
  const required = cards.reduce((width, card) => width + card.getBoundingClientRect().width, 0)
    + Math.max(0, cards.length - 1) * gap;
  const overflow = required > group.clientWidth + 0.5;
  group.classList.toggle('stats-overflow', overflow);
  group.setAttribute('aria-hidden', String(overflow));
}
if (typeof ResizeObserver !== 'undefined') {
  document.querySelectorAll('.stats.overview-stats').forEach(group => {
    const observer = new ResizeObserver(() => updateStatsVisibility(group));
    observer.observe(group);
    Array.from(group.children).forEach(card => observer.observe(card));
    new MutationObserver(() => updateStatsVisibility(group)).observe(group,
      {subtree: true, childList: true, characterData: true});
    updateStatsVisibility(group);
  });
}


window.refreshOverviewAttendance = async () => {
  if ($('dailyPage').hidden || !window.adminPermissions?.includes('overview')) return;
  const token = ++overviewToken;
  const query = '/api/admin/overview?month=' + encodeURIComponent(currentMonth())
    + (overviewWeek ? '&week=' + encodeURIComponent(overviewWeek) : '');
  const data = await api(query);
  if (token !== overviewToken || $('dailyPage').hidden) return;
  const before = overviewData?.week.days.flatMap(day => day.shifts) || [];
  const previous = new Map(before.map(s => [s.id, JSON.stringify(s)]));
  const shifts = data.week.days.flatMap(day => day.shifts);
  const sameWeek = overviewData?.week.start === data.week.start && shifts.length === before.length
    && shifts.every(s => previous.has(s.id));
  const changed = new Set(shifts.filter(s => previous.get(s.id) !== JSON.stringify(s)).map(s => s.id));
  overviewData = data;
  overviewWeek = data.week.start === data.week.current ? null : data.week.start;
  renderOverview(sameWeek ? changed : null);
  await loadAnomaly(anomalyWeeks);
  if (token !== overviewToken) return;
  if ($('status').textContent === '正在加载概览…') $('status').textContent = '';
  if ($('detailDialog').open && detailShiftId !== null) {
    const fresh = shifts.find(s => s.id === detailShiftId) || data.timeline.find(s => s.id === detailShiftId);
    if (fresh) openDetail(fresh);
  }
};
