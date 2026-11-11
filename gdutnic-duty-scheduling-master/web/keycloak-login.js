(() => {
  const buttons = [...document.querySelectorAll('[data-keycloak-login]')];
  if (!buttons.length) return;
  for (const button of buttons) {
    button.hidden = true;
    button.disabled = true;
    button.addEventListener('click', () => {
      window.location.assign('/api/auth/keycloak/start?target=' + encodeURIComponent(button.dataset.keycloakLogin));
    });
  }
  function refresh() { return fetch('/api/auth/keycloak/status', {cache: 'no-store'}).then(response => response.json()).then(data => {
    for (const button of buttons) {
      button.dataset.ssoEnabled = String(!!data.enabled);
      const registering = button.dataset.keycloakLogin === "admin" && document.querySelector("#authSubmit")?.textContent === "注册";
      button.hidden = !data.enabled || registering;
      button.disabled = !data.enabled;
      if (!data.enabled) button.title = 'GDUTNIC 登录暂不可用';
    }
  }).catch(() => { for (const button of buttons) { button.hidden = true; button.disabled = true; button.title = 'GDUTNIC 登录暂不可用'; } }); }
  window.addEventListener('sso-configuration-changed', refresh);
  window.addEventListener('pageshow', refresh);
  refresh();
})();
