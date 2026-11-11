// 数据管理：按白名单表浏览数据库，支持预览、编辑与删除单行。
// Table and column names come from the server; this file never builds SQL.
let dataTable = null;       // currently selected table name
let dataPageNo = 1;         // 1-based page
let dataPreview = null;     // last preview payload
let dataPendingKeys = null;   // primary key of the row being edited or deleted
let dataSelected = new Set(); // JSON-encoded primary keys selected on the current page
let dataAnchor = -1;          // row index shift-click ranges start from

function dataKeyOf(row) {
  const keys = {};
  for (const column of (dataPreview?.pk || [])) keys[column] = row[column];
  return keys;
}

function dataCellText(value) {
  if (value === null || value === undefined) return '';
  return String(value);
}

function renderDataTables(tables) {
  const list = $('dataTables');
  list.replaceChildren();
  for (const table of tables) {
    const button = el('button', 'data-table-item' + (table.name === dataTable ? ' active' : ''));
    button.type = 'button';
    button.append(el('span', 'data-table-name', table.label),
      el('span', 'data-table-rows', String(table.rows)));
    button.dataset.table = table.name;
    button.title = `${table.name} · ${table.rows} 行`;
    button.onclick = () => selectDataTable(table.name);
    list.append(button);
  }
}

// Selection is per visible page: changing table, page or search clears it.
function dataResetSelection() {
  dataSelected = new Set();
  dataAnchor = -1;
}

function dataRowKeyAt(index) {
  const row = dataPreview?.items?.[index];
  return row ? JSON.stringify(dataKeyOf(row)) : null;
}

function dataSyncSelectionUI() {
  const selected = dataPreview
    ? dataPreview.items.filter(row => dataSelected.has(JSON.stringify(dataKeyOf(row)))).length
    : 0;
  // Tables that refuse deletion (站点设置) hide both bulk buttons entirely.
  const canDelete = !dataPreview || dataPreview.deletable !== false;
  for (const tr of document.querySelectorAll('#dataRows tr[data-key]')) {
    tr.classList.toggle('selected', dataSelected.has(tr.dataset.key));
  }
  $('dataClearSelected').hidden = selected === 0 || !canDelete;
  $('dataClearSelected').textContent = selected ? `清除选中（${selected}）` : '清除选中';
  $('dataClearPage').disabled = !dataPreview || !dataPreview.total || !canDelete;
  $('dataClearPage').hidden = !canDelete;
}

// Click selects; shift-click extends from the last clicked row. Buttons keep working.
function dataSelectRow(index, shiftKey) {
  if (!dataPreview || !dataPreview.items[index]) return;
  const key = dataRowKeyAt(index);
  if (shiftKey && dataAnchor >= 0) {
    const [from, to] = dataAnchor < index ? [dataAnchor, index] : [index, dataAnchor];
    for (let i = from; i <= to; i++) {
      const target = dataRowKeyAt(i);
      if (target) dataSelected.add(target);
    }
  } else if (dataSelected.has(key)) {
    dataSelected.delete(key);   // clicking a selected row again deselects it
  } else {
    dataSelected.add(key);
  }
  dataAnchor = index;
  dataSyncSelectionUI();
}

function renderDataPreview() {
  const data = dataPreview;
  if (!data) return;
  const head = $('dataHead');
  head.replaceChildren();
  const headRow = el('tr');
  for (const column of data.columns) {
    const th = el('th', '', column.name);
    if (column.secret) th.classList.add('data-secret');
    if (!column.editable) th.title = '只读列';
    headRow.append(th);
  }
  headRow.append(el('th', 'data-actions-head', '操作'));
  head.append(headRow);

  const rows = $('dataRows');
  rows.replaceChildren();
  if (!data.items.length) {
    const tr = el('tr');
    const td = el('td', 'data-empty', '没有符合条件的记录');
    td.colSpan = data.columns.length + 1;
    tr.append(td);
    rows.append(tr);
  }
  data.items.forEach((row, index) => {
    const tr = el('tr');
    tr.dataset.key = JSON.stringify(dataKeyOf(row));
    tr.classList.add('data-row');
    tr.dataset.index = String(index);
    tr.addEventListener('click', e => {
      if (e.target.closest('button')) return;   // buttons keep their own behaviour
      dataSelectRow(index, e.shiftKey);
    });
    for (const column of data.columns) {
      const value = dataCellText(row[column.name]);
      // Empty cells render a dash so a blank value is distinguishable from a missing column.
      const td = el('td', value === '' ? 'data-cell is-blank' : 'data-cell', value === '' ? '—' : value);
      if (column.secret) td.classList.add('data-secret');
      if (value) td.title = value;
      tr.append(td);
    }
    const actions = el('td', 'data-actions');
    const edit = el('button', 'btn small tonal', '编辑');
    edit.type = 'button';
    edit.disabled = !data.columns.some(column => column.editable);
    edit.title = edit.disabled ? '这张表不支持在此修改' : '编辑这一行';
    edit.onclick = () => openDataEdit(row);
    actions.append(edit);
    // Protected rows (the 原始管理员, or tables such as 站点设置) get no delete button.
    if (row.deletable !== false) {
      const remove = el('button', 'btn small danger', '删除');
      remove.type = 'button';
      remove.onclick = () => openDataDelete(row);
      actions.append(remove);
    }
    tr.append(actions);
    rows.append(tr);
  });

  dataSyncSelectionUI();
  $('dataRange').textContent = `第 ${data.page} / ${data.pages} 页 · 共 ${data.total} 行`;
  $('dataPrev').disabled = data.page <= 1;
  $('dataNext').disabled = data.page >= data.pages;
}

async function loadDataTables() {
  const data = await api('/api/admin/data/tables');
  renderDataTables(data.items);
  return data.items;
}

async function loadDataPreview({keepSelection = false} = {}) {
  if (!dataTable) return;
  if (!keepSelection) dataResetSelection();
  $('dataStatus').textContent = '正在读取…';
  try {
    const params = new URLSearchParams({page: String(dataPageNo), size: '50'});
    const query = $('dataSearch').value.trim();
    if (query) params.set('q', query);
    dataPreview = await api(`/api/admin/data/tables/${encodeURIComponent(dataTable)}?${params}`);
    renderDataPreview();
    $('dataStatus').textContent = '';
  } catch (err) {
    dataPreview = null;
    dataResetSelection();
    $('dataHead').replaceChildren();
    $('dataRows').replaceChildren();
    $('dataStatus').textContent = err.message;
    dataSyncSelectionUI();
  }
}

async function selectDataTable(name) {
  dataTable = name;
  dataPageNo = 1;
  $('dataSearch').value = '';
  // Re-render so the active table is highlighted without a second request.
  for (const button of document.querySelectorAll('.data-table-item')) {
    button.classList.toggle('active', button.dataset.table === name);
  }
  await loadDataPreview();
}

// Editing covers only the columns the server marks editable; the rest stay read-only.
function openDataEdit(row) {
  if (!dataPreview) return;
  const editable = dataPreview.columns.filter(column => column.editable);
  if (!editable.length) return;
  $('dataEditTitle').textContent = `编辑「${dataPreview.label}」`;
  $('dataEditMeta').textContent = `主键：${dataPreview.pk
    .map(column => `${column}=${dataCellText(row[column])}`).join('，')}`;
  $('dataEditStatus').textContent = '';
  dataPendingKeys = dataKeyOf(row);
  const fields = $('dataEditFields');
  fields.replaceChildren();
  for (const column of editable) {
    const label = el('label');
    label.append(el('span', '', column.name));
    const value = dataCellText(row[column.name]);
    // Long text gets a textarea; everything else stays a single-line input.
    const input = value.length > 60 || value.includes('\n')
      ? el('textarea') : el('input');
    input.name = column.name;
    input.value = value;
    if (column.name === 'status' || column.name === 'special' || column.name === 'manual'
        || column.name === 'active') input.inputMode = 'numeric';
    label.append(input);
    fields.append(label);
  }
  $('dataEditDialog').showModal();
}

$('closeDataEdit').onclick = () => $('dataEditDialog').close();
$('dataEditForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const values = {};
  for (const input of form.querySelectorAll('#dataEditFields [name]')) {
    values[input.name] = input.value;
  }
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  try {
    await api(`/api/admin/data/tables/${encodeURIComponent(dataTable)}`, 'PUT',
      {keys: dataPendingKeys, values});
    $('dataEditDialog').close();
    await loadDataTables();
    await loadDataPreview();
    $('dataStatus').textContent = '已保存修改。';
  } catch (err) {
    $('dataEditStatus').textContent = err.message;
  } finally {
    button.disabled = false;
  }
};

function openDataDelete(row) {
  if (!dataPreview) return;
  dataPendingKeys = dataKeyOf(row);
  const label = dataPreview.pk
    .map(column => `${column}=${dataCellText(row[column])}`).join('，');
  $('dataDeleteBody').textContent =
    `将从「${dataPreview.label}」删除 1 行（${label}）。此操作无法撤销。`
    + '若要删除整周排班或整月外勤，请使用对应页面的批量删除。';
  $('dataDeleteStatus').textContent = '';
  $('dataDeleteDialog').showModal();
}

$('closeDataDelete').onclick = () => $('dataDeleteDialog').close();
$('dataDeleteForm').onsubmit = async e => {
  e.preventDefault();
  const button = e.currentTarget.querySelector('[type=submit]');
  button.disabled = true;
  try {
    await api(`/api/admin/data/tables/${encodeURIComponent(dataTable)}`, 'DELETE',
      {keys: dataPendingKeys});
    $('dataDeleteDialog').close();
    await loadDataTables();
    await loadDataPreview();
    $('dataStatus').textContent = '已删除 1 行。';
  } catch (err) {
    $('dataDeleteStatus').textContent = err.message;
  } finally {
    button.disabled = false;
  }
};

$('dataTab').onclick = async () => {
  showPage('data');
  if (!dataTable) {
    try {
      const tables = await loadDataTables();
      if (tables.length) await selectDataTable(tables[0].name);
    } catch (err) { $('dataStatus').textContent = err.message; }
  } else {
    await loadDataTables();
    await loadDataPreview();
  }
};
$('dataRefresh').onclick = async () => { await loadDataTables(); await loadDataPreview(); };

// Bulk deletes reuse the range-clear confirmation so a typed phrase always guards them.
$('dataClearPage').onclick = () => {
  if (!dataPreview || !dataPreview.total) return;
  const query = $('dataSearch').value.trim();
  const phrase = '清除该页';
  openRangeClear({
    title: '确认清除该页',
    phrase,
    body: `将删除「${dataPreview.label}」第 ${dataPreview.page} 页上的 `
      + `${dataPreview.items.length} 行记录`
      + (query ? `（搜索条件：${query}）` : '')
      + '。此操作无法撤销。',
    submit: () => api(`/api/admin/data/tables/${encodeURIComponent(dataTable)}/delete-page`, 'POST',
      {page: dataPreview.page, size: dataPreview.size, q: query}),
    onDone: async () => {
      await loadDataTables();
      await loadDataPreview();
      $('dataStatus').textContent = '已清除当前页。';
    },
  });
};

$('dataClearSelected').onclick = () => {
  if (!dataPreview || !dataSelected.size) return;
  const keys = dataPreview.items.filter(row => dataSelected.has(JSON.stringify(dataKeyOf(row))))
    .map(dataKeyOf);
  if (!keys.length) return;
  const phrase = '清除选中';
  openRangeClear({
    title: '确认清除选中记录',
    phrase,
    body: `将从「${dataPreview.label}」删除选中的 ${keys.length} 行记录。此操作无法撤销。`,
    submit: () => api(`/api/admin/data/tables/${encodeURIComponent(dataTable)}/delete-many`, 'POST',
      {keys}),
    onDone: async () => {
      await loadDataTables();
      await loadDataPreview();
      $('dataStatus').textContent = '已清除选中的记录。';
    },
  });
};
$('dataPrev').onclick = () => { if (dataPageNo > 1) { dataPageNo--; loadDataPreview(); } };
$('dataNext').onclick = () => {
  if (dataPreview && dataPageNo < dataPreview.pages) { dataPageNo++; loadDataPreview(); }
};
let dataSearchTimer = 0;
$('dataSearch').addEventListener('input', () => {
  clearTimeout(dataSearchTimer);
  dataSearchTimer = setTimeout(() => { dataPageNo = 1; loadDataPreview(); }, 250);
});
