(() => {
  const get = id => document.getElementById(id);
  const form = get('ssoBindForm'), dialog = get('ssoConfirm');
  let csrfToken = '';
  async function request(path, body) {
    const response = await fetch('/api/auth/keycloak/' + path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...body, csrf_token: csrfToken})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重新登录');
    return data;
  }
  request('binding').then(data => {
    csrfToken = data.csrf_token;
    get('ssoUsername').textContent = data.sso_username;
    get('ssoBindHint').textContent = '未自动匹配到本地账号，请输入已有账号的用户名和密码完成绑定。';
    get('ssoVerify').disabled = false;
  }).catch(error => { get('ssoBindStatus').textContent = error.message; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    get('ssoVerify').disabled = true;
    get('ssoBindStatus').textContent = '';
    try {
      const data = await request('verify', Object.fromEntries(new FormData(form)));
      form.elements.password.value = '';
      get('ssoConfirmMessage').textContent = `是否将 ${data.sso_username} 绑定到 ${data.local_username} ？`;
      get('ssoConfirmStatus').textContent = '';
      dialog.showModal();
    } catch (error) { get('ssoBindStatus').textContent = error.message; }
    finally { get('ssoVerify').disabled = false; }
  });
  get('ssoCancel').addEventListener('click', () => dialog.close());
  get('ssoConfirmButton').addEventListener('click', async () => {
    get('ssoConfirmButton').disabled = true;
    try {
      const data = await request('confirm', {});
      window.location.assign(data.redirect);
    } catch (error) { get('ssoConfirmStatus').textContent = error.message; }
    finally { get('ssoConfirmButton').disabled = false; }
  });
})();
