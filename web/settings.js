// Attendance window: mirrors the server's early/late/absent minute thresholds.
async function loadRules() {
  $('rulesStatus').textContent = '正在加载设置…';
  try {
    const rules = await api('/api/admin/rules');
    for (const key of ['early_minutes', 'late_minutes', 'absent_minutes']) {
      $('rulesForm').elements[key].value = rules[key];
    }
    $('rulesStatus').textContent = '';
  } catch (err) {
    $('rulesStatus').textContent = err.message;
    if (err.status === 401) $('rulesStatus').textContent = '登录状态已失效，请重新登录。';
  }
}
$('settingsTab').onclick = () => { showPage('settings'); applySettingsPermissions(); };
$('rulesForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget, submit = form.querySelector('[type=submit]');
  const body = {
    early_minutes: form.elements.early_minutes.value,
    late_minutes: form.elements.late_minutes.value,
    absent_minutes: form.elements.absent_minutes.value,
  };
  if (Number(body.absent_minutes) <= Number(body.late_minutes)) {
    $('rulesStatus').textContent = '旷班时间必须大于迟到时间，两者都从班次开始时间起算。';
    return;
  }
  submit.disabled = true;
  try {
    const saved = await api('/api/admin/rules', 'PUT', body);
    rules = saved;
    window.announceCheckinSettings?.();
    $('rulesStatus').textContent = '值班时间设置已保存，签到页面会自动使用新规则。';
  } catch (err) { $('rulesStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};

// 系统时间：自动设置，或从手动指定的小时:分钟:00 开始计时。
let clockTimer = 0;

function renderClock(state) {
  $('clockAutomatic').checked = !!state.automatic;
  $('clockManual').hidden = !!state.automatic;
  if (!state.automatic) {
    $('clockDate').value = state.date || '';
    $('clockHour').value = state.hour;
    $('clockMinute').value = state.minute;
  } else if (!$('clockDate').value) {
    $('clockDate').value = state.date || '';
  }
  paintClockNow(state.current);
}

function paintClockNow(iso) {
  const at = iso ? new Date(iso) : new Date(clockNow());
  const text = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(at);
  $('clockNow').textContent = text;
}

async function loadClock() {
  try {
    const state = await api('/api/admin/clock');
    syncClock(state);
    renderClock(state);
  } catch (err) { $('clockStatus').textContent = err.message; }
}

// Tick locally between server reads so the clock reads like a real clock.
function tickClock() {
  if (!$('clockNow') || $('settingsPage').hidden) return;
  // Derive from the page clock so date rollovers are handled correctly.
  paintClockNow(new Date(clockNow()).toISOString());
}

$('clockAutomatic').onchange = () => { $('clockManual').hidden = $('clockAutomatic').checked; };
$('clockForm').onsubmit = async e => {
  e.preventDefault();
  const submit = e.currentTarget.querySelector('[type=submit]');
  submit.disabled = true;
  $('clockStatus').textContent = '';
  try {
    const automatic = $('clockAutomatic').checked;
    const state = await api('/api/admin/clock', 'PUT', {
      automatic,
      date: automatic ? undefined : $('clockDate').value,
      hour: Number($('clockHour').value || 0),
      minute: Number($('clockMinute').value || 0),
    });
    syncClock(state);
    renderClock(state);
    window.announceCheckinSettings?.();
    $('clockStatus').textContent = automatic
      ? '已恢复自动设置，使用真实时间。'
      : `已设为手动时间，从 ${state.date} ${String(state.hour).padStart(2, '0')}:${String(state.minute).padStart(2, '0')}:00 开始计时。`;
  } catch (err) { $('clockStatus').textContent = err.message; }
  finally { submit.disabled = false; }
};

// Each card follows its own permission: 值班时间 needs `rules`, 系统时间 needs `clock`.
// A missing permissions list means an older backend, so everything stays visible.
function applySettingsPermissions() {
  const can = key => window[key] !== false;
  if ($('rulesForm')) $('rulesForm').hidden = !can('canRules');
  if ($('sessionTimeoutForm')) $('sessionTimeoutForm').hidden = !can('canRules');
  if ($('clockForm')) $('clockForm').hidden = !can('canClock');
  if ($('smtpForm')) $('smtpForm').hidden = !can('canSmtp');
  const held = window.adminPermissions;
  const allowed = key => !Array.isArray(held) || held.includes(key);
  $('checkinSettingsSection').hidden = !allowed('checkin_manage');
  $('ssoSettingsSection').hidden = !allowed('sso');
  if ($('settingsPage').hidden) return;
  if (allowed('checkin_manage')) window.loadCheckinAccess?.();
  if (allowed('sso')) window.loadSsoSettings?.();
  if (can('canSmtp')) loadSmtp();
  if (can('canRules')) { loadRules(); loadSessionTimeout(); }
  if (can('canClock')) {
    loadClock();
    clearInterval(clockTimer);
    clockTimer = setInterval(tickClock, 1000);
  } else {
    clearInterval(clockTimer);
  }
}
window.applySettingsPermissions = applySettingsPermissions;

// A restored session can resolve before the settings modules finish loading.
document.addEventListener('DOMContentLoaded', () => {
  const wasVisible = !$('settingsPage').hidden;
  if (Array.isArray(window.adminPermissions)) window.applyPermissions?.(window.adminPermissions);
  if (wasVisible) applySettingsPermissions();
}, {once: true});

const originalSettingsClick = $('settingsTab').onclick;
$('settingsTab').onclick = () => {
  originalSettingsClick();
};


// 原始管理员自行改密；接口只接受 root 本人，其它账号拿到 403。
$('rootPasswordForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const password = $('rootNewPassword').value;
  const confirm = $('rootConfirmPassword').value;
  const status = $('rootPasswordStatus');
  if (password !== confirm) { status.textContent = '两次输入的密码不一致。'; return; }
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  status.textContent = '';
  try {
    await api('/api/admin/root-password', 'POST', {password});
    form.reset();
    status.textContent = '密码已更新，请使用新密码重新登录。';
  } catch (err) {
    status.textContent = err.message;
  } finally {
    button.disabled = false;
  }
};


// SMTP 发件邮箱：保存后服务端从不回显明文密码，只表示是否已设置。
function paintSmtp(data) {
  $('smtpHost').value = data.host || '';
  $('smtpPort').value = data.port || '';
  $('smtpSecurity').value = data.security || 'ssl';
  $('smtpUsername').value = data.username || '';
  $('smtpPassword').value = '';
  $('smtpPassword').placeholder = data.has_password ? '已保存，留空表示不修改' : '尚未设置';
  $('smtpFromAddress').value = data.from_address || '';
  $('smtpFromName').value = data.from_name || '';
  $('smtpEnabled').checked = !!data.enabled;
}

async function loadSmtp() {
  $('smtpStatus').textContent = '正在加载 SMTP 配置…';
  try {
    paintSmtp(await api('/api/admin/smtp'));
    $('smtpStatus').textContent = '';
  } catch (err) {
    $('smtpStatus').textContent = err.message;
  }
}

$('smtpForm').onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget, submit = form.querySelector('[type=submit]');
  // Address validation lives on the server, so a bad value always explains itself.
  submit.disabled = true;
  $('smtpStatus').textContent = '正在保存…';
  try {
    const body = {
      host: $('smtpHost').value.trim(),
      port: $('smtpPort').value,
      security: $('smtpSecurity').value,
      username: $('smtpUsername').value.trim(),
      from_address: $('smtpFromAddress').value.trim(),
      from_name: $('smtpFromName').value.trim(),
      enabled: $('smtpEnabled').checked,
    };
    // An empty box means "keep the stored password" rather than "clear it".
    if ($('smtpPassword').value) body.password = $('smtpPassword').value;
    paintSmtp(await api('/api/admin/smtp', 'POST', body));
    $('smtpStatus').textContent = 'SMTP 设置已保存。';
  } catch (err) {
    $('smtpStatus').textContent = err.message;
  } finally {
    submit.disabled = false;
  }
};

$('smtpTest').onclick = async () => {
  const to = window.prompt('测试邮件收件地址', $('smtpFromAddress').value || '');
  if (!to) return;
  const button = $('smtpTest');
  button.disabled = true;
  $('smtpStatus').textContent = '正在发送测试邮件…';
  try {
    const result = await api('/api/admin/smtp/test', 'POST', {to});
    $('smtpStatus').textContent = `测试邮件已发送到 ${result.to}，请检查收件箱。`;
  } catch (err) {
    $('smtpStatus').textContent = err.message;
  } finally {
    button.disabled = false;
  }
};

async function loadSessionTimeout() {
  try {
    const value = await api('/api/admin/session-timeout');
    for (const key of ['member_minutes', 'admin_minutes', 'member_response_seconds', 'admin_response_seconds']) $('sessionTimeoutForm').elements[key].value = value[key];
    $('sessionTimeoutStatus').textContent = '';
  } catch (error) { $('sessionTimeoutStatus').textContent = error.message; }
}
$('sessionTimeoutForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget, submit = form.querySelector('[type=submit]');
  submit.disabled = true;
  try {
    await api('/api/admin/session-timeout', 'PUT', {
      member_minutes: Number(form.elements.member_minutes.value),
      admin_minutes: Number(form.elements.admin_minutes.value),
      member_response_seconds: Number(form.elements.member_response_seconds.value),
      admin_response_seconds: Number(form.elements.admin_response_seconds.value)
    });
    $('sessionTimeoutStatus').textContent = '自动退出设置已保存，对当前登录会话立即生效。';
    window.dispatchEvent(new Event('session-timeout-changed'));
  } catch (error) { $('sessionTimeoutStatus').textContent = error.message; }
  finally { submit.disabled = false; }
});
