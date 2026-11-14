// One-off shifts outside the weekly template; administrators create and edit them here.
// editingSpecial is declared in schedule.js so the shared shift dialog can reuse it.
let specialItems = [];
const specialToday = today();

function specialCard(s) {
  const card = el('article', 'card'), top = el('div', 'shift-top'), info = el('div');
  info.append(el('div', 'time', `${s.day} ${s.start} — ${s.end < s.start ? '次日 ' : ''}${s.end}`));
  if (s.note) info.append(el('div', 'location', s.note));
  top.append(info, el('span', 'badge', `${s.members.filter(m => m.checked_at).length} / ${s.members.length} 已签到`));
  card.append(top);
  const members = el('div', 'members');
  for (const m of s.members) {
    const n = el('span', 'member' + (m.checked_at ? ' checked' : ''), m.name + (m.checked_at ? ' · 已签到' : ' · 待签到'));
    if (m.checked_at) n.title = new Date(m.checked_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'});
    members.append(n);
  }
  card.append(members);
  const actions = el('div', 'actions');
  actions.append(button('编辑', 'secondary', () => openSpecial(s)), button('删除', 'delete', () => {
    openDeleteConfirm({
      title: '删除特殊排班',
      body: `${s.day} ${s.start} — ${s.end < s.start ? '次日 ' : ''}${s.end}\n${s.note ? s.note + '\n' : ''}删除后，该排班及其签到记录将被永久删除，无法恢复。`,
      submit: () => api('/api/admin/special-schedules/' + s.id, 'DELETE'),
      onDone: loadSpecials,
    });
  }));
  card.append(actions);
  return card;
}

async function loadSpecials() {
  $('specialStatus').textContent = '正在加载特殊排班…';
  try {
    const data = await api('/api/special-schedules');
    specialItems = admin ? data.items : data.items.filter(s => s.members.some(m => m.name === member?.username));
    $('specialCount').textContent = specialItems.length;
    $('specialPeople').textContent = specialItems.reduce((n, s) => n + s.members.length, 0);
    $('specialList').replaceChildren();
    if (!specialItems.length) {
      const box = el('div', 'empty');
      box.append(el('h2', '', '暂无未来特殊排班'), el('p', '', '点击「新增特殊排班」安排临时值班。'));
      $('specialList').append(box);
    }
    for (const s of specialItems) $('specialList').append(specialCard(s));
    $('specialStatus').textContent = '';
  } catch (err) {
    $('specialStatus').textContent = err.message;
    if (err.status === 401) $('specialStatus').textContent = '登录状态已失效，请重新登录。';
  }
}

function openSpecial(s) {
  // 0 = creating a new special shift; null is reserved for the normal daily editor.
  editingSpecial = s ? s.id : 0;
  const f = $('shiftForm');
  f.reset(); $('formStatus').textContent = '';
  $('editorTitle').textContent = s ? '编辑特殊排班' : '新增特殊排班';
  for (const key of ['day', 'start', 'end', 'note']) {
    f.elements[key].value = s ? s[key] : (key === 'day' ? specialToday : '');
  }
  f.elements.members.value = s ? s.members.map(m => m.name).join('\n') : '';
  $('editor').showModal();
}

function bootSpecial() {
  $('specialTab').onclick = async () => {
    showPage('special');
    await loadSpecials();
  };
  $('refreshSpecial').onclick = loadSpecials;
  $('addSpecial').onclick = () => openSpecial(null);
}
bootSpecial();
