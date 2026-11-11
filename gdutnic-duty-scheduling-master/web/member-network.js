(() => {
  window.networkAccess = {checkin: false, field: false};
  window.applyNetworkButton = (button, kind, text) => {
    const blocked = window.networkAccess[kind] !== true;
    const permission = kind === 'checkin' ? 'duty' : 'member_field';
    const denied = window.memberPermissions ? !window.memberPermissions.has(permission) : false;
    button.classList.toggle('permission-blocked', denied);
    button.classList.toggle('network-blocked', blocked);
    button.title = blocked ? `当前网络不允许${kind === 'checkin' ? '打卡' : '外勤登记'}，请连接允许的网络` : '';
    if (denied) button.title = kind === 'checkin' ? '暂无打卡权限' : '暂无外勤权限';
    if (blocked || denied) {
      button.disabled = true;
      button.classList.add('is-locked');
      if (text) text.textContent = denied ? (kind === 'checkin' ? '暂无打卡权限' : '暂无外勤权限') : '当前网络不可用';
    }
  };
  window.setNetworkAccess = access => {
    if (!access) return;
    window.networkAccess = access;
  };
  let busy = false;
  async function refresh() {
    if (document.hidden || busy) return;
    busy = true;
    const previous = window.networkAccess;
    try {
      const response = await fetch('/api/network-access', {cache:'no-store'});
      if (!response.ok) throw new Error('网络校验失败');
      window.setNetworkAccess(await response.json());
    } catch (_) { window.networkAccess = {checkin:false, field:false}; }
    finally {
      busy = false;
      if (previous.checkin !== window.networkAccess.checkin || previous.field !== window.networkAccess.field) {
        window.dispatchEvent(new Event('network-access-change'));
      }
    }
  }
  setInterval(refresh, 30000);
  window.refreshNetworkAccess = refresh;
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', refresh);
  refresh();
})();
