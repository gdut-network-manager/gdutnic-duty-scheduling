(() => {
  const get = id => document.getElementById(id);
  const admin = document.body.dataset.admin === 'true';
  const role = admin ? 'admin' : 'member';
  const form = get(admin ? 'authForm' : 'memberLoginForm');
  const status = get(admin ? 'authStatus' : 'memberLoginStatus');
  const submit = form.querySelector('[type=submit]');
  const action = form.dataset.registering === 'true' ? 'register' : 'login';
  if (!window.fetch || !window.AbortSignal?.timeout) return;

  async function request(path, options) {
    const response = await fetch('/api/' + role + '/' + path, {
      cache: 'no-store', signal: AbortSignal.timeout(15000), ...options,
    });
    if (response.status === 405) throw new Error('登录请求被拒绝（HTTP 405），请刷新后重试。');
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(typeof data?.detail === 'string' ? data.detail : `登录服务请求失败（HTTP ${response.status}）`);
    }
    if (!data) throw new Error('登录服务返回了无法识别的响应，请稍后重试。');
    return data;
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    status.textContent = '正在登录…';
    try {
      await request(action, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: form.elements.username.value, password: form.elements.password.value}),
      });
      form.reset();
      const session = await request('status');
      if (!session.logged_in) {
        throw new Error('浏览器未建立登录会话，请检查 Cookie 和访问地址后重试。');
      }
      location.replace(form.action);
    } catch (error) {
      status.textContent = error.name === 'TimeoutError' ? '登录服务响应超时，请重试。' : error.message;
      submit.disabled = false;
    }
  });
})();
