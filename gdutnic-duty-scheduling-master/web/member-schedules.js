// Read-only weekly board: names only, with one stable colour for every shift.
let memberListWeek = null, memberListData = null, memberListToken = 0;
const LIST_WEEK_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
function renderMemberSchedules(week) {
  const board = $('memberWeekBoard');
  board.gapObserver?.disconnect();
  board.replaceChildren();
  $('listWeekRange').textContent = `${week.start} 至 ${week.end}`;
  $('listWeekPrev').disabled = !week.previous;
  $('listWeekNext').disabled = !week.next;
  if (!week.days.some(day => day.shifts.length)) {
    board.append(el('p', 'hint', '本周暂无排班。'));
    return;
  }
  const header = el('div', 'week-row week-head-row');
  header.append(el('div', 'week-day-head', '星期'));
  week.days.forEach(day => header.append(el('div', 'week-day-head', `${LIST_WEEK_NAMES[day.weekday]} ${day.day.slice(5)}`)));
  board.append(header);
  const rows = weekBoardRows(week.days);
  const regular = [];
  rows.forEach(item => {
    const row = el('div', 'week-row' + (item.special ? ' week-special-row' : ''));
    row.append(el('div', 'week-slot-label', item.label));
    item.cells.forEach(shifts => {
      const cell = el('div', 'week-cell');
      shifts.forEach(shift => {
        const card = el('article', 'week-card member-schedule-card');
        card.append(el('strong', 'week-card-time', `${shift.start} — ${shift.end < shift.start ? '次日 ' : ''}${shift.end}`));
        shift.members.forEach(name => card.append(el('span', 'member-schedule-name', name)));
        cell.append(card);
      });
      row.append(cell);
    });
    if (!item.special) regular.push(row);
    board.append(row);
  });
  if (rows.some(row => row.special)) {
    const update = () => board.style.setProperty('--week-unit', `${Math.max(...regular.map(row => row.getBoundingClientRect().height))}px`);
    update();
    board.gapObserver = new ResizeObserver(update);
    regular.forEach(row => board.gapObserver.observe(row));
  }
}
async function loadMemberSchedules() {
  const token = ++memberListToken;
  $('scheduleListStatus').textContent = '正在加载排班列表…';
  try {
    const week = await api('/api/member/schedule-list' + (memberListWeek ? '?week=' + encodeURIComponent(memberListWeek) : ''));
    if (token !== memberListToken || !member) return;
    memberListData = week;
    memberListWeek = week.start;
    renderMemberSchedules(week);
    $('scheduleListStatus').textContent = '';
  } catch (err) {
    if (token !== memberListToken) return;
    $('memberWeekBoard').replaceChildren();
    $('scheduleListStatus').textContent = err.message;
    if (err.status === 401) await bootMember();
  }
}
$('scheduleListTab').onclick = () => { showMemberPage('scheduleList'); loadMemberSchedules(); };
$('listWeekPrev').onclick = () => { if (memberListData?.previous) { memberListWeek = memberListData.previous; loadMemberSchedules(); } };
$('listWeekNext').onclick = () => { if (memberListData?.next) { memberListWeek = memberListData.next; loadMemberSchedules(); } };
$('listWeekToday').onclick = () => { memberListWeek = null; loadMemberSchedules(); };
