let permissionGroups = [], permissionTree = [];
let expandedPermissionGroups = new Set();

function renderPermissionGroups() {
  const list = $('permissionGroupList'); list.replaceChildren();
  for (const group of permissionGroups) {
    const card = el('details', 'card permission-group');
    card.dataset.groupId = group.id;
    card.open = expandedPermissionGroups.has(group.id);
    card.addEventListener('toggle', () => {
      if (card.open) expandedPermissionGroups.add(group.id);
      else expandedPermissionGroups.delete(group.id);
    });
    const summary = el('summary', 'permission-group-summary');
    const heading = el('div', 'permission-group-heading');
    const title = el('div', 'permission-group-title');
    title.append(el('h2', '', group.name));
    if (group.default) title.append(el('span', 'permission-tag', '默认权限组'));
    heading.append(title, el('p', 'hint', `${group.member_count} 位成员 · 已开放 ${group.permissions.length} 项权限`));
    summary.append(heading); card.append(summary);
    const form = el('form', 'permission-group-form');
    const label = el('label', '', '权限组名称');
    const name = el('input'); name.value = group.name; name.maxLength = 64; name.required = true;
    name.readOnly = group.default; name.setAttribute('aria-label', '权限组名称'); label.append(name);
    const hint = el('p', 'hint', group.default ? '所有新账号默认加入会员组。修改访问范围会对组内所有成员生效。' : '组内成员共享以下访问范围。展开成员端或管理端，选择可访问的页面及可使用的操作。');
    const tree = buildPermissionTree(permissionTree, group.permissions);
    const status = el('p', 'permission-group-status'); status.setAttribute('role', 'status');
    const actions = el('div', 'actions');
    const save = button('保存访问范围', 'primary', () => {}); save.type = 'submit'; save.dataset.icon = 'content-save-outline';
    save.disabled = true;
    actions.append(save);
    if (!group.default) {
      const remove = button('删除权限组', 'delete', async () => {
        if (!confirm(`删除权限组「${group.name}」？组内 ${group.member_count} 位成员将自动移入会员组。`)) return;
        remove.disabled = true;
        try {
          await api('/api/admin/permission-groups/' + encodeURIComponent(group.id), 'DELETE');
          await refreshAdminPermissions();
          if (window.adminPermissions.includes('permission_groups')) await loadPermissionGroups();
        } catch (error) { status.textContent = error.message; }
        finally { remove.disabled = false; }
      });
      remove.dataset.icon = 'delete-outline'; actions.append(remove);
    }
    form.append(label, hint, tree, status, actions); card.append(form);
    const dirty = () => { save.disabled = name.value === group.name
      && [...readPermissionTree(tree)].sort().join(',') === [...group.permissions].sort().join(','); };
    form.addEventListener('input', dirty); form.addEventListener('change', dirty);
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled = true; status.textContent = '正在保存…';
      try {
        const saved = await api('/api/admin/permission-groups/' + encodeURIComponent(group.id), 'PUT',
          {name: name.value.trim(), permissions: readPermissionTree(tree)});
        group.name = saved.name; group.permissions = saved.permissions;
        title.querySelector('h2').textContent = saved.name;
        heading.querySelector('p').textContent = `${group.member_count} 位成员 · 已开放 ${saved.permissions.length} 项权限`;
        status.textContent = '已保存，组内所有成员的访问范围已更新。';
        await refreshAdminPermissions();
      } catch (error) { status.textContent = error.message; dirty(); }
    };
    list.append(card);
  }
}

async function loadPermissionGroups() {
  $('permissionGroupsStatus').textContent = '正在加载权限组…';
  try {
    const data = await api('/api/admin/permission-groups');
    permissionGroups = data.items; permissionTree = data.tree;
    renderPermissionGroups(); $('permissionGroupsStatus').textContent = '';
  } catch (error) { $('permissionGroupsStatus').textContent = error.message; }
}
$('permissionGroupsTab').onclick = () => { showPage('permissionGroups'); loadPermissionGroups(); };
$('addPermissionGroup').onclick = () => {
  $('permissionGroupCreateForm').reset(); $('permissionGroupCreateStatus').textContent = '';
  $('permissionGroupCreateDialog').showModal();
};
$('closePermissionGroupCreate').onclick = () => $('permissionGroupCreateDialog').close();
$('permissionGroupCreateForm').onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  try {
    const group = await api('/api/admin/permission-groups', 'POST', {name: form.elements.name.value.trim(), permissions: []});
    expandedPermissionGroups.add(group.id);
    $('permissionGroupCreateDialog').close();
    await loadPermissionGroups();
    const card = [...$('permissionGroupList').children].find(node => node.dataset.groupId === group.id);
    if (card) { card.scrollIntoView({block: 'nearest', behavior: 'smooth'}); card.querySelector('input').focus(); }
  } catch (error) { $('permissionGroupCreateStatus').textContent = error.message; }
  finally { submit.disabled = false; }
};
