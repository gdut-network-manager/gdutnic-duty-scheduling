// Duty swaps: two people exchange their shifts after agreeing with each other.
const WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

function shiftLabel(shift) {
  const day = new Date(`${shift.day}T12:00:00+08:00`);
  const weekday = WEEKDAY_NAMES[(day.getUTCDay() + 6) % 7];
  const cross = shift.end < shift.start ? '次日 ' : '';
  return `${shift.day} ${weekday} ${shift.start} — ${cross}${shift.end}`;
}

function swapCard(item) {
  const card = el('article', 'card swap-card');
  const top = el('div', 'shift-top');
  top.append(el('h2', '', `${item.a.person} ⇄ ${item.b.person}`));
  top.append(el('span', 'badge', item.deletable ? '待执行' : '进行中'));
  card.append(top);
  const grid = el('div', 'swap-pair');
  for (const [side, label] of [[item.a, '甲方'], [item.b, '乙方']]) {
    const line = el('div', 'swap-side');
    line.append(el('span', 'swap-role', label), el('strong', '', side.person));
    line.append(el('span', 'swap-when', shiftLabel(side.shift)));
    if (side.shift.note) line.append(el('span', 'swap-note', side.shift.note));
    grid.append(line);
  }
  card.append(grid);
  const actions = el('div', 'actions');
  const remove = button('删除', 'delete', () => {
    if (!item.deletable) return;
    openDeleteConfirm({
      title: '删除调班记录',
      body: `${item.a.person}：${shiftLabel(item.a.shift)}\n${item.b.person}：${shiftLabel(item.b.shift)}\n删除后，双方将恢复原班次。`,
      submit: () => api('/api/admin/swaps/' + item.id, 'DELETE'),
      onDone: async () => {
        await loadSwaps();
        $('swapStatus').textContent = '调班已删除，双方恢复原班次。';
      },
    });
  });
  remove.disabled = !item.deletable;
  if (!item.deletable) remove.title = '已有班次开始，无法删除';
  actions.append(remove);
  card.append(actions);
  return card;
}

async function loadSwaps() {
  $('swapStatus').textContent = '正在加载调班记录…';
  try {
    const data = await api('/api/admin/swaps');
    const list = $('swapList');
    list.replaceChildren();
    if (!data.items.length) {
      const box = el('div', 'empty');
      box.append(el('h2', '', '暂无调班记录'), el('p', '', '点击「添加调班」为两位值班人员互换班次。'));
      list.append(box);
    }
    for (const item of data.items) list.append(swapCard(item));
    $('swapStatus').textContent = '';
  } catch (err) { $('swapStatus').textContent = err.message; }
}

async function fillPeople(select) {
  const data = await api('/api/admin/members');
  const names = (data.items || []).filter(m => m.active).map(m => m.username).sort();
  select.replaceChildren(el('option', '', '选择人员'));
  select.options[0].value = '';
  for (const name of names) {
    const option = el('option', '', name);
    option.value = name;
    select.append(option);
  }
}

async function fillCandidates(select, person, excludeShiftId) {
  select.replaceChildren(el('option', '', '正在加载…'));
  if (!person) {
    select.replaceChildren(el('option', '', '请先选择人员'));
    select.disabled = true;
    return;
  }
  try {
    const data = await api('/api/admin/swaps/candidates?username=' + encodeURIComponent(person));
    const items = (data.items || []).filter(shift => String(shift.id) !== String(excludeShiftId));
    select.replaceChildren();
    if (!items.length) {
      // Cross-week swaps are allowed; the usual cause is simply no future shifts.
      select.append(el('option', '', '暂无可用班次（需未来且未参与调班）'));
      $('swapFormStatus').textContent =
        '该人员没有可调换的未来班次。可先在「每周排班」生成更多日期，或确认其班次尚未开始。';
    } else if ($('swapFormStatus').textContent.startsWith('该人员没有可调换')) {
      $('swapFormStatus').textContent = '';
    }
    for (const shift of items) {
      const option = el('option', '', `${shiftLabel(shift)}${shift.note ? ' · ' + shift.note : ''}`);
      option.value = String(shift.id);
      select.append(option);
    }
    select.disabled = !items.length;
  } catch (err) {
    select.replaceChildren(el('option', '', err.message));
    select.disabled = true;
  }
}

async function openSwapDialog() {
  const form = $('swapForm');
  form.reset();
  $('swapFormStatus').textContent = '';
  $('swapShiftA').disabled = true;
  $('swapShiftB').disabled = true;
  await fillPeople($('swapPersonA'));
  await fillPeople($('swapPersonB'));
  $('swapDialog').showModal();
}

$('swapTab').onclick = () => { showPage('swap'); loadSwaps(); };
$('addSwap').onclick = openSwapDialog;
$('closeSwap').onclick = () => $('swapDialog').close();
$('swapPersonA').onchange = () => fillCandidates($('swapShiftA'), $('swapPersonA').value);
$('swapPersonB').onchange = () => fillCandidates($('swapShiftB'), $('swapPersonB').value);

$('swapForm').onsubmit = async e => {
  e.preventDefault();
  const submit = e.currentTarget.querySelector('[type=submit]');
  const nameA = $('swapPersonA').value, nameB = $('swapPersonB').value;
  const shiftA = $('swapShiftA').value, shiftB = $('swapShiftB').value;
  const message = !nameA || !nameB ? '请选择双方人员'
    : !shiftA || !shiftB ? '请选择双方的班次'
    : nameA === nameB ? '请选择两位不同的人员'
    : !$('swapConfirmed').checked ? '请先勾选“双方已协商完成并同意调班”' : '';
  if (message) { $('swapFormStatus').textContent = message; return; }
  submit.disabled = true;
  try {
    await api('/api/admin/swaps', 'POST', {
      shift_a: Number(shiftA), name_a: nameA, shift_b: Number(shiftB), name_b: nameB,
      confirmed: true,
    });
    $('swapDialog').close();
    await loadSwaps();
    $('swapStatus').textContent = '调班已添加，双方班次已互换。';
  } catch (err) { $('swapFormStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};
