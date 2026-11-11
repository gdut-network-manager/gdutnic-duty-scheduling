// A small radio-card dialog shared by the overview and field-duty pages.
const EDIT_STATUS_CHOICES = {
  attendance: [
    {id: 'normal', label: '准时'},
    {id: 'late', label: '迟到'},
    {id: 'absent', label: '旷班'},
  ],
  field: [
    {id: 'yes', label: '已参加外勤'},
    {id: 'no', label: '未参加外勤'},
  ],
};

let editStatusSubmit = null;

function openStatusEditor({title, meta, kind, current, extraNote, onSave}) {
  $('editStatusTitle').textContent = title;
  $('editStatusMeta').textContent = meta || '';
  $('editStatusNote').textContent = extraNote || '';
  const box = $('editStatusOptions');
  box.replaceChildren();
  const choices = EDIT_STATUS_CHOICES[kind] || [];
  for (const choice of choices) {
    const label = el('label', 'status-option');
    const input = el('input');
    input.type = 'radio';
    input.name = 'status';
    input.value = choice.id;
    input.checked = choice.id === current;
    label.append(input, el('span', '', choice.label));
    box.append(label);
  }
  editStatusSubmit = onSave;
  $('editStatusDialog').showModal();
}

$('closeEditStatus').onclick = () => $('editStatusDialog').close();
$('editStatusForm').onsubmit = async e => {
  e.preventDefault();
  const chosen = $('editStatusForm').querySelector('input[name=status]:checked');
  if (!chosen) { $('editStatusNote').textContent = '请选择一个状态。'; return; }
  const submit = e.currentTarget.querySelector('[type=submit]');
  submit.disabled = true;
  try {
    await editStatusSubmit(chosen.value);
    $('editStatusDialog').close();
  } catch (err) {
    $('editStatusNote').textContent = err.message;
  } finally {
    submit.disabled = false;
  }
};
