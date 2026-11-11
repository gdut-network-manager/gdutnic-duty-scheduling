// Admin field-duty board: who registered this month and who has not.
// The month is tracked in JS like the overview's week; there is no month input.
let fieldMonth = today().slice(0, 7);
// How many records the visible month holds, for the delete confirmation.
let fieldClearCount = 0;

function fieldCard(item, month) {
  const card = el('article', `field-person ${item.registered ? 'done' : 'missing'} clickable`);
  card.append(el('strong', '', item.username));
  card.append(el('span', '', item.registered
    ? new Date(item.registered_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'})
    : '未参加外勤'));
  if (item.manual) card.append(el('span', 'manual-flag', '管理员修改'));
  card.title = '点击更改外勤参与状态';
  card.onclick = () => editParticipation(item, month);
  return card;
}

function editParticipation(item, month) {
  if (!window.canEditField) return;
  openStatusEditor({
    title: '更改外勤状态',
    meta: `${item.username} · ${month}`,
    kind: 'field',
    current: item.registered ? 'yes' : 'no',
    extraNote: '改为已参加时若无登记时间，将使用当前时间记录。',
    onSave: async value => {
      await api('/api/admin/field-duty', 'PUT',
        {username: item.username, registered: value === 'yes', month});
      await loadFieldBoard();
      $('fieldAdminStatus').textContent = `已更新「${item.username}」的外勤状态。`;
    },
  });
}

// 2026-11 reads better than a bare input value in the board heading.
function monthLabel(month) {
  return `${month.slice(0, 4)} 年 ${Number(month.slice(5))} 月`;
}

async function loadFieldBoard() {
  const month = fieldMonth;
  $('fieldAdminStatus').textContent = '正在加载外勤记录…';
  try {
    const data = await api('/api/admin/field-duty?month=' + encodeURIComponent(month));
    $('fieldRegistered').textContent = data.registered;
    const isCurrent = data.month === data.current;
    // The heading would be wrong while browsing another month.
    $('fieldTitle').textContent = isCurrent ? '本月外勤' : '当月外勤';
    $('fieldRange').textContent = isCurrent ? `${monthLabel(data.month)}（本月）` : monthLabel(data.month);
    $('fieldSummary').textContent = isCurrent
      ? `本月已参加外勤 / 共 ${data.total} 人`
      : `${monthLabel(data.month)} 已参加外勤 / 共 ${data.total} 人`;
    // The server returns the neighbouring months that actually exist.
    $('fieldPrev').disabled = !data.previous;
    $('fieldNext').disabled = !data.next;
    $('fieldPrev').title = data.previous ? `上个月（${data.previous}）` : '已到最早月份';
    $('fieldNext').title = data.next ? `下个月（${data.next}）` : '已到最晚月份';
    $('fieldToday').hidden = isCurrent;
    // Any stored row — confirmed or still waiting — can be cleared.
    $('fieldClear').disabled = !data.stored;
    $('fieldClear').title = data.stored
      ? `删除 ${monthLabel(data.month)}（${data.stored} 条记录）`
      : `${monthLabel(data.month)}没有外勤记录`;
    fieldClearCount = data.stored;
    fieldMonth = data.month;
    const board = $('fieldBoard');
    board.replaceChildren();
    if (!data.items.length) board.append(el('p', 'hint', `${monthLabel(data.month)} 暂无可用账号。`));
    for (const item of data.items) board.append(fieldCard(item, data.month));
    $('fieldAdminStatus').textContent = '';
  } catch (err) {
    $('fieldAdminStatus').textContent = err.message;
  }
}

// Step one month; the arrows are disabled once no further month holds records.
function shiftFieldMonth(delta) {
  const [year, month] = fieldMonth.split('-').map(Number);
  const total = year * 12 + month - 1 + delta;
  fieldMonth = `${String(Math.floor(total / 12)).padStart(4, '0')}-${String(total % 12 + 1).padStart(2, '0')}`;
  loadFieldBoard();
}

$('fieldTab').onclick = () => { showPage('field'); loadFieldBoard(); };
$('fieldPrev').onclick = () => shiftFieldMonth(-1);
$('fieldNext').onclick = () => shiftFieldMonth(1);
$('fieldToday').onclick = () => { fieldMonth = today().slice(0, 7); loadFieldBoard(); };

// Reset one whole month of field-duty records; this is a status, not an archive.
$('fieldClear').onclick = () => {
  if (!fieldClearCount) return;
  const month = fieldMonth;
  const phrase = '删除本月外勤';
  openRangeClear({
    title: '确认删除整月外勤',
    phrase,
    body: `将删除 ${monthLabel(month)}的 ${fieldClearCount} 条外勤记录，`
      + '相关人员回到“未登记”，需要重新登记并由他人确认。此操作不写入归档，无法撤销。',
    submit: () => api(`/api/admin/field-duty/month?month=${encodeURIComponent(month)}`, 'DELETE'),
    onDone: async () => {
      await loadFieldBoard();
      $('fieldAdminStatus').textContent = `已删除 ${monthLabel(month)}的外勤记录。`;
    },
  });
};
