let memberItems = [], deletingMember = '', memberMeta = {groups: [], me: '', can_grant: false};
let memberQuery = '';
const unlinkingSso = new Set();
const memberMatches = member => !memberQuery || member.username.toLowerCase().includes(memberQuery);

function renderMembers() {
  $('memberList').replaceChildren();
  const visible = memberItems.filter(memberMatches);
  for (const member of visible) {
    const row = el('article', 'card member-account'), title = el('div');
    title.append(el('h2', '', member.username), el('p', '', member.active ? '账号已启用' : '账号已停用'));
    const tags = el('div', 'permission-tags');
    tags.append(el('span', 'permission-tag', member.group_name || '会员'));
    if (member.sso_bound) tags.append(el('span', 'permission-tag', '已绑定 GDUTNIC'));
    title.append(tags);
    const actions = el('div', 'actions');
    actions.append(
      ...(memberMeta.can_grant ? [button('更改权限', 'secondary', () => openPermission(member))] : []),
      button('更改密码', 'secondary', () => openPassword(member)),
      button('重置密码', 'secondary', async () => {
        if (!confirm(`将「${member.username}」的密码重置为 gg2017？`)) return;
        try { await api('/api/admin/members', 'PATCH', {username:member.username, reset_password:true}); $('membersStatus').textContent = '密码已重置，该用户需重新登录。'; }
        catch (err) { $('membersStatus').textContent = err.message; }
      }),
      button(member.active ? '停用账号' : '启用账号', member.active ? 'secondary' : 'primary', async () => {
        try { await api('/api/admin/members', 'PATCH', {username:member.username, active:!member.active}); await loadMembers(); }
        catch (err) { $('membersStatus').textContent = err.message; }
      }),
      button('删除账号', 'delete', async () => {
        if (deletingMember === member.username) return;
        if (!confirm(`确认删除「${member.username}」的账号？该操作无法撤销，已有排班和签到记录会保留。`)) return;
        deletingMember = member.username;
        try { await api('/api/admin/members?username=' + encodeURIComponent(member.username), 'DELETE'); await loadMembers(); $('membersStatus').textContent = `已删除「${member.username}」的账号。`; }
        catch (err) { $('membersStatus').textContent = err.message; }
        finally { deletingMember = ''; }
      })
    );
    if (member.sso_bound) actions.prepend(button('解绑SSO', 'delete', async () => {
      if (unlinkingSso.has(member.username)) return;
      if (!confirm(`确认解除「${member.username}」的 GDUTNIC SSO 绑定？该账号的 SSO 登录将失效，重新绑定需验证本地密码。`)) return;
      unlinkingSso.add(member.username);
      try {
        await api('/api/admin/members/sso?username=' + encodeURIComponent(member.username), 'DELETE');
        await loadMembers();
        $('membersStatus').textContent = `已解除「${member.username}」的 SSO 绑定。`;
      } catch (error) { $('membersStatus').textContent = error.message; }
      finally { unlinkingSso.delete(member.username); }
    }));
    row.append(title, actions);
    $('memberList').append(row);
  }
  const filtered = memberQuery ? `，筛选出 ${visible.length} 个` : '';
  $('membersStatus').textContent = `共 ${memberItems.length} 个账号${filtered}${memberMeta.can_grant ? '' : '；当前账号不能更改他人权限'}。`;
  if (!visible.length && memberItems.length) {
    const empty = el('div', 'empty');
    empty.append(el('h2', '', '没有匹配的账号'), el('p', '', `没有姓名包含“${memberQuery}”的账号。`));
    $('memberList').append(empty);
  }
}

async function loadMembers() {
  if (Array.isArray(window.adminPermissions) &&
      !window.adminPermissions.includes('members')) {
    $('membersStatus').textContent = '当前账号没有用户管理权限。';
    return;
  }
  $('membersStatus').textContent = '正在加载用户…';
  try {
    const data = await api('/api/admin/members');
    // Defence in depth: the server already omits the 原始管理员, never render it either.
    memberItems = (data.items || []).filter(item => item && item.root !== true
      && item.username !== '原始管理员');
    memberMeta = {groups: data.groups || [],
      me: data.me || '', can_grant: !!data.can_grant};
    renderMembers();
  } catch (err) {
    $('membersStatus').textContent = err.message;
    if (err.status === 401) $('membersStatus').textContent = '登录状态已失效，请重新登录。';
  }
}

function updateMemberGroupPreview() {
  const group = memberMeta.groups.find(item => item.id === $('permissionGroupSelect').value);
  $('permissionGroupPreview').textContent = group
    ? `${group.name} · 可访问 ${group.permissions.length} 个页面。修改组内访问范围请前往权限组管理。` : '';
}
function openPermission(member) {
  $('permissionTitle').textContent = `更改权限 · ${member.username}`;
  $('permissionStatus').textContent = '';
  const select = $('permissionGroupSelect'); select.replaceChildren();
  for (const group of memberMeta.groups) {
    const option = el('option', '', group.name + (group.default ? '（默认）' : ''));
    option.value = group.id; select.append(option);
  }
  select.value = member.group_id;
  updateMemberGroupPreview();
  $('permissionForm').dataset.username = member.username;
  $('permissionDialog').showModal();
}
$('permissionGroupSelect').onchange = updateMemberGroupPreview;

function openPassword(member) {
  $('memberPasswordForm').reset();
  $('memberPasswordTitle').textContent = `更改密码 · ${member.username}`;
  $('memberPasswordStatus').textContent = '';
  $('memberPasswordForm').dataset.username = member.username;
  $('memberPasswordDialog').showModal();
}

$('membersTab').onclick = () => {
  showPage('members');
  loadMembers();
};
// Filtering is local: the account list is already loaded, so typing never re-queries.
$('memberSearch').addEventListener('input', e => {
  memberQuery = e.target.value.trim().toLowerCase();
  if (memberItems.length) {
    renderMembers();
  } else {
    loadMembers();
  }
});
$('addMember').onclick = () => { $('memberForm').reset(); $('memberFormStatus').textContent = ''; $('memberEditor').showModal(); };
$('closeMember').onclick = () => $('memberEditor').close();
$('closePermission').onclick = () => $('permissionDialog').close();
$('closeMemberPassword').onclick = () => $('memberPasswordDialog').close();

$('memberForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget, submit = form.querySelector('[type=submit]');
  submit.disabled = true;
  try {
    const name = form.elements.username.value.trim();
    await api('/api/admin/members', 'POST', {username:name});
    // Close the dialog only after the request succeeds; errors stay visible inside it.
    $('memberEditor').close();
    await loadMembers();
    $('membersStatus').textContent = `账号「${name}」已创建，初始密码为 gg2017。`;
  } catch (err) { $('memberFormStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};

$('permissionForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget, submit = form.querySelector('[type=submit]');
  const groupId = $('permissionGroupSelect').value;
  submit.disabled = true;
  try {
    await api('/api/admin/members/permissions', 'PUT', {username:form.dataset.username, group_id:groupId});
    $('permissionDialog').close();
    await refreshAdminPermissions();
    if (window.adminPermissions.includes('members')) await loadMembers();
    $('membersStatus').textContent = `已更新「${form.dataset.username}」所属的权限组。`;
  } catch (err) { $('permissionStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};

$('memberPasswordForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget, submit = form.querySelector('[type=submit]');
  submit.disabled = true;
  try {
    await api('/api/admin/members/password', 'PUT', {
      username: form.dataset.username,
      password: form.elements.password.value,
    });
    const name = form.dataset.username;
    $('memberPasswordDialog').close();
    $('membersStatus').textContent = `已更改「${name}」的密码，该账号需重新登录。`;
  } catch (err) { $('memberPasswordStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};

$('cleanupMembers').onclick = async () => {
  if (!confirm('确认清理默认会员组中未出现在已保存的每周排班或任何已生成排班中的用户？删除账号无法撤销，已有排班和签到记录会保留。')) return;
  const button = $('cleanupMembers'); button.disabled = true;
  try {
    const result = await api('/api/admin/members/cleanup', 'POST');
    await loadMembers();
    $('membersStatus').textContent = `已清理 ${result.deleted} 个多余用户。`;
  } catch (err) { $('membersStatus').textContent = err.message; }
  finally { button.disabled = false; }
};
