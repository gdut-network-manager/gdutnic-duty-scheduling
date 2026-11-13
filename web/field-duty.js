// Member-side field duty (外勤) registration for the current month.
// Field registration shares the member overview with attendance.
function showMemberPage(page) {
  if ($('scheduleListPage')) $('scheduleListPage').hidden = page !== 'scheduleList';
  if ($('scheduleListTab')) $('scheduleListTab').classList.toggle('active', page === 'scheduleList');
  $('dutyPage').hidden = page !== 'duty';
  const confirm = $('fieldConfirmPage');
  if (confirm) confirm.hidden = page !== 'confirm';
  $('dutyTab').classList.toggle('active', page === 'duty');
  if ($('fieldConfirmTab')) $('fieldConfirmTab').classList.toggle('active', page === 'confirm');
}

// Three states: 0 点击登记 (blue) → 1 等待确认 (yellow) → 2 登记完成 (green, locked).
const FIELD_IDLE = '点击登记';
const FIELD_HOVER = '确认登记';
const FIELD_PENDING = '等待确认';
const FIELD_DONE = '登记完成';

function setFieldLabel(text) {
  const label = $('fieldButtonText');
  if (label) label.textContent = text;
}

function paintFieldStatus(data) {
  window.setNetworkAccess?.(data.network_access);
  const button = $('fieldButton');
  const status = Number(data.status || 0);
  $('fieldCount').textContent = status === 2 ? '1' : '0';
  button.classList.toggle('registered', status === 2);
  button.classList.toggle('pending', status === 1);
  setFieldLabel(status === 2 ? FIELD_DONE : status === 1 ? FIELD_PENDING
    : (button.matches(':hover') ? FIELD_HOVER : FIELD_IDLE));
  // Registering is a one-way 0 → 1 step; the button stays disabled afterwards.
  button.disabled = status !== 0;
  window.applyNetworkButton?.(button, 'field', status === 0 ? $('fieldButtonText') : null);
  const when = data.registered_at
    ? new Date(data.registered_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) : '';
  $('fieldStatus').textContent = status === 2 ? `本月已于 ${when} 登记并确认外勤。`
    : status === 1 ? `本月已于 ${when} 登记，等待确认。` : '';
}

async function loadFieldDuty() {
  if ($('fieldButton').classList.contains('is-submitting')) return;
  $('fieldButton').disabled = true;
  $('fieldStatus').textContent = '';
  try { paintFieldStatus(await api('/api/member/field-duty')); }
  catch (err) { $('fieldStatus').textContent = err.message; }
}

const fieldIdle = () => !$('fieldButton').classList.contains('registered')
  && !$('fieldButton').classList.contains('pending') && !$('fieldButton').disabled;
$('fieldButton').addEventListener('mouseenter', () => { if (fieldIdle()) setFieldLabel(FIELD_HOVER); });
$('fieldButton').addEventListener('mouseleave', () => { if (fieldIdle()) setFieldLabel(FIELD_IDLE); });
// Pressing shows the confirm copy immediately; the pending copy lands with the response.
$('fieldButton').addEventListener('pointerdown', () => { if (fieldIdle()) setFieldLabel(FIELD_HOVER); });

$('dutyTab').onclick = () => { showMemberPage('duty'); load(); loadFieldDuty(); };
$('fieldButton').onclick = async () => {
  const button = $('fieldButton');
  if (button.disabled || button.classList.contains('registered') || button.classList.contains('pending')) return;
  try {
    button.classList.add('is-submitting');
    button.disabled = true;
    await playRegistrationAnimation(button);
    const result = await api('/api/member/field-duty', 'POST');
    button.classList.remove('is-submitting');
    paintFieldStatus(result);
  } catch (err) {
    button.classList.remove('is-submitting', 'registered');
    $('fieldStatus').textContent = err.message;
    setFieldLabel(button.matches(':hover') ? FIELD_HOVER : FIELD_IDLE);
    await loadFieldDuty();
    $('fieldStatus').textContent = err.message;
  } finally {
    button.classList.remove('is-submitting');
  }
};

window.addEventListener('network-access-change', () => {
  if (!$('workspace').hidden && !$('dutyPage').hidden) loadFieldDuty();
});

$('refresh').onclick = () => { load(); loadFieldDuty(); };
