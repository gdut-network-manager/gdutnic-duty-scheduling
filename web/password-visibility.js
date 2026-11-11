// Shared by the member and administrator login portals.
document.querySelectorAll('#memberLoginForm input[name="password"], #authForm input[name="password"]').forEach((input, index) => {
  const field = input.closest('.outlined-field');
  if (!field) return;
  field.classList.add('password-field');
  if (!input.id) input.id = `login-password-${index}`;
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'password-visibility';
  toggle.setAttribute('aria-controls', input.id);
  const setVisible = visible => {
    input.type = visible ? 'text' : 'password';
    toggle.dataset.icon = visible ? 'eye-off-outline' : 'eye-outline';
    toggle.title = visible ? '隐藏密码' : '显示密码';
    toggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    toggle.setAttribute('aria-pressed', String(visible));
  };
  setVisible(false);
  toggle.addEventListener('click', event => {
    event.preventDefault();
    setVisible(input.type === 'password');
  });
  input.form?.addEventListener('reset', () => setVisible(false));
  field.append(toggle);
});
