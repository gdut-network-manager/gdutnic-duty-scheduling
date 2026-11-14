let studentImportResult = null, studentCsvBusy = false;

function setStudentCsvBusy(busy) {
  studentCsvBusy = busy;
  for (const id of ['downloadStudentIds', 'importStudentIds', 'confirmStudentImport', 'ignoreStudentImport', 'replaceStudentImport']) {
    $(id).disabled = busy;
  }
}
async function studentCsvResponse(response) {
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === 'string' ? data.detail : '名单处理失败，请重试');
  }
  return response;
}
$('downloadStudentIds').onclick = async () => {
  if (studentCsvBusy) return;
  setStudentCsvBusy(true);
  try {
    const response = await studentCsvResponse(await fetch('/api/admin/members/student-ids/export'));
    const url = URL.createObjectURL(await response.blob());
    const link = el('a');
    link.href = url; link.download = '学号名单.csv';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) { $('membersStatus').textContent = err.message; }
  finally { setStudentCsvBusy(false); }
};
$('importStudentIds').onclick = () => {
  if (!studentCsvBusy) $('studentIdsFile').click();
};
$('studentIdsFile').onchange = async e => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || studentCsvBusy) return;
  if (file.size > 2 * 1024 * 1024) { $('membersStatus').textContent = 'CSV 文件不能超过 2 MB'; return; }
  setStudentCsvBusy(true);
  try {
    const response = await studentCsvResponse(await fetch('/api/admin/members/student-ids/import', {
      method: 'POST', headers: {'Content-Type': 'text/csv'}, body: file,
    }));
    studentImportResult = await response.json();
    renderStudentImportResult();
    $('studentImportDialog').showModal();
    await loadMembers();
  } catch (err) { $('membersStatus').textContent = err.message; }
  finally { setStudentCsvBusy(false); }
};
function renderStudentImportResult() {
  const result = studentImportResult, conflicts = result.conflicts;
  const title = $('studentImportTitle');
  title.textContent = conflicts.length ? '遇到冲突' : '导入成功';
  title.dataset.icon = conflicts.length ? 'close-circle-outline' : 'check-circle-outline';
  $('studentImportDialog').classList.toggle('has-conflicts', !!conflicts.length);
  $('studentImportSummary').textContent = conflicts.length
    ? `成功导入${result.updated}个项目，有${conflicts.length}个冲突项`
    : `已成功导入${result.updated}个用户`;
  $('studentImportNotes').textContent = `已略过${result.skipped || 0}行空编号或相同编号，${result.unmatched || 0}行未匹配到账号。`;
  $('studentImportStatus').textContent = '';
  const list = $('studentImportConflicts'); list.replaceChildren();
  for (const conflict of conflicts) {
    const row = el('div', 'student-import-conflict');
    row.append(el('strong', '', conflict.username),
      el('span', 'student-id-old', conflict.original_id || '（无编号）'),
      el('span', '', '→'), el('span', 'student-id-new', conflict.student_id));
    if (conflict.reason && conflict.reason !== '已有编号与导入编号不一致') row.append(el('small', 'student-conflict-reason', conflict.reason));
    list.append(row);
  }
  $('confirmStudentImport').hidden = !!conflicts.length;
  $('ignoreStudentImport').hidden = !conflicts.length;
  $('replaceStudentImport').hidden = !conflicts.length;
}
function closeStudentImport() {
  if (!studentCsvBusy) $('studentImportDialog').close();
}
$('confirmStudentImport').onclick = closeStudentImport;
$('ignoreStudentImport').onclick = closeStudentImport;
$('studentImportDialog').addEventListener('cancel', e => { if (studentCsvBusy) e.preventDefault(); });
$('replaceStudentImport').onclick = async () => {
  if (studentCsvBusy || !studentImportResult?.conflicts.length) return;
  setStudentCsvBusy(true);
  $('studentImportStatus').textContent = '';
  try {
    const result = await api('/api/admin/members/student-ids/replace', 'POST', {items: studentImportResult.conflicts});
    studentImportResult.updated += result.updated;
    studentImportResult.conflicts = result.conflicts;
    renderStudentImportResult();
    await loadMembers();
  } catch (err) { $('studentImportStatus').textContent = err.message; }
  finally { setStudentCsvBusy(false); }
};
