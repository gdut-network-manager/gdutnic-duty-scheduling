// Shared in-page confirmation for actions that require a second step.
let pendingDelete = null;
let deleteBusy = false;

function openDeleteConfirm({title, body, submit, onDone, context = '', confirmLabel = '确认删除', confirmIcon = 'delete-outline'}) {
  const dialog = $('deleteConfirmDialog');
  if (dialog.open || deleteBusy) return;
  pendingDelete = {submit, onDone};
  dialog.dataset.context = context;
  $('deleteConfirmTitle').textContent = title;
  $('deleteConfirmBody').textContent = body;
  $('deleteConfirmStatus').textContent = '';
  const confirm = $('deleteConfirmForm').querySelector('[type=submit]');
  confirm.textContent = confirmLabel;
  confirm.dataset.icon = confirmIcon;
  dialog.showModal();
}

$('cancelDeleteConfirm').onclick = () => {
  if (!deleteBusy) $('deleteConfirmDialog').close();
};
$('deleteConfirmDialog').addEventListener('cancel', e => {
  if (deleteBusy) e.preventDefault();
});
$('deleteConfirmDialog').addEventListener('close', event => {
  if (!event.currentTarget.open) pendingDelete = null;
});
$('deleteConfirmForm').onsubmit = async e => {
  e.preventDefault();
  if (!pendingDelete || deleteBusy) return;
  const task = pendingDelete;
  const buttons = e.currentTarget.querySelectorAll('button');
  deleteBusy = true;
  buttons.forEach(button => { button.disabled = true; });
  $('deleteConfirmStatus').textContent = '';
  try {
    await task.submit();
  } catch (err) {
    $('deleteConfirmStatus').textContent = err.message;
    return;
  } finally {
    deleteBusy = false;
    buttons.forEach(button => { button.disabled = false; });
  }
  pendingDelete = null;
  $('deleteConfirmDialog').close();
  await task.onDone();
};
