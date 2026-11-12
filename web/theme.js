(() => {
  'use strict';
  const key = 'gdutnic-theme';
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const valid = value => value === 'light' || value === 'dark';
  let preference;
  try { preference = localStorage.getItem(key); } catch (_) { /* Storage may be disabled. */ }
  if (!valid(preference)) preference = null;

  function apply() {
    const dark = (preference || (system.matches ? 'dark' : 'light')) === 'dark';
    root.dataset.theme = dark ? 'dark' : 'light';
    const label = dark ? '深色' : '浅色';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.dataset.icon = dark ? 'weather-night' : 'weather-sunny';
      const description = `当前${label}模式，切换为${dark ? '浅色' : '深色'}`;
      button.setAttribute('aria-label', description);
      button.setAttribute('aria-pressed', String(dark));
      button.title = description;
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = dark ? '#0F1417' : '#186078';
  }
  apply(); // Apply before the first paint, including the login screen.
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => {
        preference = root.dataset.theme === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem(key, preference); } catch (_) { /* Keep the in-memory choice. */ }
        apply();
      });
    });
  });
  const followSystem = () => { if (!preference) apply(); };
  if (system.addEventListener) system.addEventListener('change', followSystem);
  else system.addListener(followSystem);
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : null;
    apply();
  });
})();
