// Connected single-select status buttons shared by overview and field-duty pages.
const EDIT_STATUS_CHOICES = {
  attendance: [
    {id: 'normal', label: '准时', icon: 'check-circle-outline'},
    {id: 'late', label: '迟到', icon: 'clock-alert-outline'},
    {id: 'absent', label: '旷班', icon: 'close-circle-outline'},
  ],
  field: [
    {id: 'yes', label: '已参加', icon: 'check-circle-outline'},
    {id: 'no', label: '未参加', icon: 'close-circle-outline'},
  ],
};

let editStatusSubmit = null;

function openStatusEditor({title, meta, kind, current, extraNote, onSave}) {
  $('editStatusTitle').textContent = title;
  $('editStatusMeta').textContent = meta || '';
  $('editStatusNote').textContent = extraNote || '';
  const box = $('editStatusOptions');
  box.replaceChildren();
  box.dataset.kind = kind;
  box.setAttribute('role', 'radiogroup');
  box.setAttribute('aria-label', title);
  const choices = EDIT_STATUS_CHOICES[kind] || [];
  for (const choice of choices) {
    const label = el('label', 'status-option');
    const input = el('input');
    input.type = 'radio';
    input.name = 'status';
    input.value = choice.id;
    input.checked = choice.id === current;
    const content = el('span', 'status-option-content', choice.label);
    content.dataset.icon = choice.icon;
    label.append(input, content);
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
