// 外勤确认：确认其他人“等待确认”的外勤登记，或删除错误登记。
// The confirm control reuses the same animated button as 外勤管理's 点击登记.
function fieldPendingCard(item) {
  const card = el('article', 'card field-pending');
  const top = el('div', 'shift-top');
  const info = el('div');
  info.append(el('div', 'time', item.username));
  info.append(el('div', 'location', `登记时间：${new Date(item.registered_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'})}`));
  const state = el('span', 'badge', '等待确认');
  top.append(info, state);
  card.append(top);

  const row = el('div', 'field-pending-actions');
  const {btn, text} = animatedButton('点击确认', '确认登记', async (button, label) => {
    button.disabled = true;
    try {
      await api('/api/member/field-duty/pending/' + encodeURIComponent(item.username), 'POST');
      // Turn green in place, then remove the card after a short pause.
      button.classList.add('registered');
      label.textContent = '已确认';
      state.textContent = '已确认';
      state.classList.add('confirmed');
      $('fieldConfirmStatus').textContent = `已确认「${item.username}」的外勤登记。`;
      setTimeout(() => { card.remove(); showFieldPendingEmpty(); }, 900);
    } catch (err) {
      $('fieldConfirmStatus').textContent = err.message;
      button.disabled = false;
    }
  });
  const remove = button('删除', 'delete', async () => {
    if (!confirm(`确认删除「${item.username}」的外勤登记？该记录将变为未登记。`)) return;
    try {
      await api('/api/member/field-duty/pending/' + encodeURIComponent(item.username), 'DELETE');
      card.remove();
      showFieldPendingEmpty();
      $('fieldConfirmStatus').textContent = `已删除「${item.username}」的外勤登记。`;
    } catch (err) { $('fieldConfirmStatus').textContent = err.message; }
  });
  row.append(btn, remove);
  card.append(row);
  return card;
}

function showFieldPendingEmpty() {
  const list = $('fieldPendingList');
  if (list.querySelector('.field-pending')) return;
  list.replaceChildren();
  const box = el('div', 'empty');
  box.append(el('h2', '', '暂无待确认的外勤'), el('p', '', '有同学登记外勤后会显示在这里。'));
  list.append(box);
}

async function loadFieldPending() {
  $('fieldConfirmStatus').textContent = '正在加载待确认外勤…';
  try {
    const data = await api('/api/member/field-duty/pending');
    const list = $('fieldPendingList');
    list.replaceChildren();
    if (!data.items.length) showFieldPendingEmpty();
    else for (const item of data.items) list.append(fieldPendingCard(item));
    $('fieldConfirmStatus').textContent = '';
  } catch (err) { $('fieldConfirmStatus').textContent = err.message; }
}

$('fieldConfirmTab').onclick = () => {
  showMemberPage('confirm');
  loadFieldPending();
};
$('refreshFieldPending').onclick = loadFieldPending;
