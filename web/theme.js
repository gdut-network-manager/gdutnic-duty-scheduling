(() => {
  'use strict';
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;

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
    if (meta) meta.content = dark ? '#0F1417' : '#1B6D88';
  }
  apply(); // Apply before the first paint, including the login screen.
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => {
        preference = root.dataset.theme === 'dark' ? 'light' : 'dark';
        apply();
      });
    });
  });
  const followSystem = () => { if (!preference) apply(); };
  if (system.addEventListener) system.addEventListener('change', followSystem);
  else system.addListener(followSystem);
  window.addEventListener('pageshow', event => {
    if (event.persisted) {
      preference = null;
      apply();
    }
  });
})();
