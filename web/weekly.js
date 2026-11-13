// The weekly table is a draft until an administrator supplies times and publishes it.
let weeklyLoaded = false;
const weekdays = ['周一','周二','周三','周四','周五','周六','周日'];
function showPage(page) {
  const pages = {daily:'dailyPage', weekly:'weeklyPage', special:'specialPage', swap:'swapPage', members:'membersPage', field:'fieldPage', settings:'settingsPage', logs:'logsPage', data:'dataPage', permissionGroups:'permissionGroupsPage', push:'pushPage', about:'aboutPage'};
  const tabs = {daily:'dailyTab', weekly:'weeklyTab', special:'specialTab', swap:'swapTab', members:'membersTab', field:'fieldTab', settings:'settingsTab', logs:'logsTab', data:'dataTab', permissionGroups:'permissionGroupsTab', push:'pushTab', about:'aboutTab'};
  for (const [key, id] of Object.entries(pages)) { $(id).hidden = key !== page; }
  for (const [key, id] of Object.entries(tabs)) { $(id).classList.toggle('active', key === page); }
}
function showSchedulePage(weekly) { showPage(weekly ? 'weekly' : 'daily'); }
function drawWeekly(roster) {
  $('weeklyReviewed').checked = !roster.needs_review;
  $('weeklyTimes').replaceChildren();
  $('weeklyGrid').replaceChildren();
  roster.slots.forEach((slot, i) => {
    const box = el('fieldset', 'weekly-slot');
    box.append(el('legend', '', slot.label));
    ['start', 'end'].forEach(key => {
      const label = el('label', '', key === 'start' ? '开始时间' : '结束时间');
      const input = el('input'); input.type = 'time'; input.id = `weekly-${key}-${i}`; input.value = slot[key];
      label.append(input); box.append(label);
    });
    $('weeklyTimes').append(box);
    const row = el('tr'), head = el('th', '', slot.label); head.scope = 'row'; row.append(head);
    slot.days.forEach((names, day) => {
      const cell = el('td'), input = el('textarea');
      input.id = `weekly-members-${i}-${day}`;
      input.rows = 4; input.value = names.join('\n');
      input.setAttribute('aria-label', `${weekdays[day]}${slot.label}人员`);
      input.placeholder = '暂无人员'; cell.append(input); row.append(cell);
    });
    $('weeklyGrid').append(row);
  });
}
function readWeekly() {
  return {
    needs_review: !$('weeklyReviewed').checked,
    slots: Array.from({length:4}, (_, i) => ({
      start: $(`weekly-start-${i}`).value, end: $(`weekly-end-${i}`).value,
      days: Array.from({length:7}, (_, day) => $(`weekly-members-${i}-${day}`).value.split(/[\n,，、]+/).map(n=>n.trim()).filter(Boolean))
    }))
  };
}
$('dailyTab').onclick = () => { showSchedulePage(false); load(); };
$('weeklyTab').onclick = async () => {
  showSchedulePage(true);
  if (weeklyLoaded) return;
  $('weeklyStatus').textContent = '正在加载每周排班…';
  try {
    drawWeekly(await api('/api/admin/weekly-roster')); weeklyLoaded = true;
    $('weeklyStatus').textContent = '';
  } catch (err) { $('weeklyStatus').textContent = err.message; }
};
$('weeklyForm').onsubmit = async e => {
  e.preventDefault(); if (!weeklyLoaded) return;
  const submit = e.currentTarget.querySelector('[type=submit]'); submit.disabled = true;
  try {
    const saved = await api('/api/admin/weekly-roster', 'PUT', readWeekly());
    const extra = saved.members_created ? `，并为 ${saved.members_created} 位新人员创建了账号（初始密码 gg2017）` : '';
    $('weeklyStatus').textContent = `每周排班已保存${extra}。生成后可在每日排班中查看和单独调整。`;
  } catch (err) { $('weeklyStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};
// Use the Shanghai date, independently of the browser timezone.
$('generateFrom').min = today();
$('generateFrom').value = today();
$('generateUntil').min = today();
$('generateUntil').value = today();
// Clearing schedules is destructive: a typed confirmation card guards the request.
const CLEAR_PHRASE = '清除所有排班';
$('clearSchedules').onclick = () => {
  $('clearForm').reset(); $('clearStatus').textContent = '';
  $('clearDialog').showModal();
};
$('closeClear').onclick = () => $('clearDialog').close();
$('clearForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget, submit = form.querySelector('[type=submit]');
  if (form.elements.confirm.value.trim() !== CLEAR_PHRASE) {
    $('clearStatus').textContent = `请准确输入“${CLEAR_PHRASE}”后再确认。`;
    return;
  }
  submit.disabled = true;
  try {
    const result = await api('/api/admin/schedules', 'DELETE');
    $('clearDialog').close();
    await load();
    $('weeklyStatus').textContent = `已清除 ${result.deleted} 个班次，其中 ${result.archived} 条考勤记录已归档保留，可继续查询出勤历史。`;
  } catch (err) { $('clearStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};
$('publishForm').onsubmit = async e => {
  e.preventDefault(); if (!weeklyLoaded) return;
  const submit = e.currentTarget.querySelector('[type=submit]');
  if (!$('weeklyReviewed').checked) { $('weeklyStatus').textContent = '请先核对图片中的人员姓名并勾选确认。'; return; }
  const start = $('generateFrom').value;
  const end = $('generateUntil').value;
  if (!start) { $('weeklyStatus').textContent = '请选择排班开始日期。'; return; }
  if (start < today()) { $('weeklyStatus').textContent = '开始日期不能早于今天。'; return; }
  if (!end) { $('weeklyStatus').textContent = '请选择排班结束日期。'; return; }
  if (end < start) { $('weeklyStatus').textContent = '结束日期不能早于开始日期。'; return; }
  submit.disabled = true;
  try {
    await api('/api/admin/weekly-roster', 'PUT', readWeekly());
    const result = await api('/api/admin/weekly-roster/publish', 'POST', {start_date:start, end_date:end});
    const note = result.created === 0 && result.skipped === 0
      ? '所选区间内没有需要生成的班次。'
      : `新增或覆盖 ${result.created} 个班次，跳过 ${result.skipped} 个已有人打卡的班次。`;
    $('weeklyStatus').textContent = `已按每周排班生成 ${start} 至 ${end}：${note}`;
    await load();
  } catch (err) { $('weeklyStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};

$('completeMembers').onclick = async () => {
  const button = $('completeMembers'); button.disabled = true;
  try {
    const result = await api('/api/admin/members/provision', 'POST');
    $('weeklyStatus').textContent = `已按已保存的排班表补全用户：新增 ${result.created} 个，已存在 ${result.existing} 个；新用户初始密码为 gg2017。`;
  } catch (err) { $('weeklyStatus').textContent = err.message; }
  finally { button.disabled = false; }
};
