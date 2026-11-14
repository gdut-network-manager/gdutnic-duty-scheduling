// One settings-page FAB, using the existing card save functions and server permissions.
(() => {
  const cards = [
    ['rulesForm','rules','值班时间'], ['sessionTimeoutForm','rules','自动退出'],
    ['clockForm','clock','系统时间'], ['smtpForm','smtp','SMTP'],
    ['checkinAccessForm','checkin_manage','打卡网络'], ['ssoConfigForm','sso','SSO'],
    ['rootPasswordForm','root','管理员密码'], // Password rotation can revoke the session: save it last.
  ];
  const baseline = new Map();
  let busy = false;
  const snapshot = form => JSON.stringify([...form.elements]
    .filter(input => input.matches('input,select,textarea') && !['button','submit','hidden'].includes(input.type))
    .map(input => [input.id || input.name,input.type === 'checkbox' || input.type === 'radio' ? input.checked : input.value]));
  window.markSettingsClean = id => { const form = $(id); if(form) baseline.set(id,snapshot(form)); };
  window.settingsIsDirty = id => baseline.has(id) && baseline.get(id) !== snapshot($(id));
  window.markSettingsClean('rootPasswordForm');
  const allowed = permission => permission === 'root' ? window.isRootAdmin === true
    : Array.isArray(window.adminPermissions) && window.adminPermissions.includes(permission);
  async function saveAll() {
    if (busy) return;
    const pending = cards.filter(([id,permission]) => allowed(permission) && window.settingsIsDirty(id));
    const status = $('settingsSaveAllStatus');
    if (!pending.length) { status.textContent = '没有需要保存的更改。'; return; }
    for (const [id] of pending) {
      const form = $(id);
      if (!form.checkValidity()) {
        form.scrollIntoView({block:'center'});form.reportValidity();
        status.textContent = '请先检查卡片中的必填项和格式。';return;
      }
    }
    busy = true;
    const button = $('settingsSaveAll'); button.disabled = true;
    button.setAttribute('aria-busy','true');
    status.textContent = '正在保存更改…';
    const controls = cards.flatMap(([id]) => [...$(id).elements].map(input=>[input,input.disabled]));
    controls.forEach(([input]) => {input.disabled=true;});
    const saved = [], failed = [];
    let skipped = 0;
    try {
      for (const [id,permission,label] of pending) {
        if (!allowed(permission)) { skipped++;continue; }
        try {
          const form = $(id);
          const result = id === 'ssoConfigForm' ? await window.saveSsoSettings()
            : await form.onsubmit({preventDefault(){},currentTarget:form});
          if (result === true) { window.markSettingsClean(id);saved.push(label); }
          else if (result?.status === 403) skipped++;
          else failed.push(label);
        } catch (error) {
          if (error.status === 403) skipped++;
          else failed.push(label);
        }
      }
      status.textContent = `已保存 ${saved.length} 项设置。${skipped ? `已跳过 ${skipped} 项无权限设置。` : ''}${failed.length ? `未保存：${failed.join('、')}，请查看卡片内提示后重试。` : ''}`;
    } finally {
      controls.forEach(([input,disabled]) => {input.disabled=disabled;});
      busy=false;button.disabled=false;button.removeAttribute('aria-busy');
    }
  }
  $('settingsSaveAll').onclick = saveAll;
  // Enter inside a settings form also uses the unified save; card test buttons stay separate.
  for (const [id] of cards) $(id).addEventListener('submit',event=>{
    event.preventDefault();event.stopImmediatePropagation();saveAll();
  },true);
})();
