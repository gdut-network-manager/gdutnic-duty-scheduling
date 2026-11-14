// Shared in-page confirmation for deleting schedules, duty swaps, or accounts.
let pendingDelete = null;
let deleteBusy = false;

function openDeleteConfirm({title, body, submit, onDone, context = ''}) {
  const dialog = $('deleteConfirmDialog');
  if (dialog.open || deleteBusy) return;
  pendingDelete = {submit, onDone};
  dialog.dataset.context = context;
  $('deleteConfirmTitle').textContent = title;
  $('deleteConfirmBody').textContent = body;
  $('deleteConfirmStatus').textContent = '';
  dialog.showModal();
}

$('cancelDeleteConfirm').onclick = () => {
  if (!deleteBusy) $('deleteConfirmDialog').close();
};
$('deleteConfirmDialog').addEventListener('cancel', e => {
  if (deleteBusy) e.preventDefault();
});
$('deleteConfirmDialog').addEventListener('close', () => { pendingDelete = null; });
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
