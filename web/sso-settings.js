(() => {
  const field = id => document.getElementById(id);
  let savedSecret = false;
  function updateFields() {
    field('ssoSecretField').hidden = field('ssoClientType').value !== 'confidential';
    field('ssoClearSecretRow').hidden = !savedSecret;
    field('ssoSecretHint').textContent = savedSecret ? '已保存密钥。留空保持不变，填写新值可替换；密钥不会回显。' : '填写 Keycloak 为此客户端生成的密钥。';
    field('ssoCustomClaimField').hidden = field('ssoClaimSelect').value !== 'custom';
  }
  function paint(value) {
    field('ssoEnabled').checked = value.enabled;
    field('ssoIssuer').value = value.issuer || '';
    field('ssoClientId').value = value.client_id || '';
    field('ssoClientType').value = value.client_type || 'public';
    field('ssoRedirectUri').value = value.redirect_uri || '';
    const standard = ['', 'preferred_username', 'name'];
    field('ssoClaimSelect').value = standard.includes(value.claim) ? value.claim : 'custom';
    field('ssoCustomClaim').value = standard.includes(value.claim) ? '' : value.claim;
    field('ssoClientSecret').value = '';
    field('ssoClearSecret').checked = false;
    savedSecret = value.has_secret;
    updateFields();
  }
  function body() {
    return {enabled: field('ssoEnabled').checked, issuer: field('ssoIssuer').value,
      client_id: field('ssoClientId').value, client_type: field('ssoClientType').value,
      secret: field('ssoClientSecret').value, clear_secret: field('ssoClearSecret').checked,
      redirect_uri: field('ssoRedirectUri').value,
      claim: field('ssoClaimSelect').value === 'custom' ? field('ssoCustomClaim').value.trim() : field('ssoClaimSelect').value};
  }
  async function action(test) {
    field('ssoTest').disabled = field('ssoSave').disabled = true;
    field('ssoConfigStatus').textContent = test ? '正在检查身份认证服务…' : '正在保存…';
    try {
      if (field('ssoClaimSelect').value === 'custom' && !field('ssoCustomClaim').value.trim()) throw new Error('请填写自定义字段名称，或选择不自动匹配。');
      const value = await api(test ? '/api/admin/sso/test' : '/api/admin/sso', test ? 'POST' : 'PUT', body());
      if (test) field('ssoConfigStatus').textContent = value.message;
      else { paint(value); window.dispatchEvent(new Event('sso-configuration-changed')); field('ssoConfigStatus').textContent = value.enabled ? 'SSO 配置已保存，登录页已显示 GDUTNIC 登录入口。' : 'SSO 配置已保存，登录页不显示 GDUTNIC 登录入口。'; }
      return true;
    } catch (error) { field('ssoConfigStatus').textContent = error.message; return {status:error.status}; }
    finally { field('ssoTest').disabled = field('ssoSave').disabled = false; }
  }
  window.loadSsoSettings = async () => {
    if (window.settingsIsDirty?.('ssoConfigForm')) return;
    field('ssoFields').disabled = true;
    field('ssoConfigStatus').textContent = '正在读取 SSO 配置…';
    try { paint(await api('/api/admin/sso')); field('ssoConfigStatus').textContent = ''; window.markSettingsClean?.('ssoConfigForm'); }
    catch (error) { field('ssoConfigStatus').textContent = error.message; }
    // Re-enable even after a failed read, so the operator can retry or save.
    finally { field('ssoFields').disabled = false; }
  };
  window.saveSsoSettings = () => action(false);
  field('ssoClientType').addEventListener('change', updateFields);
  field('ssoClaimSelect').addEventListener('change', updateFields);
  field('ssoTest').addEventListener('click', () => action(true));
  field('ssoConfigForm').addEventListener('submit', event => { event.preventDefault(); action(false); });
})();
