(() => {
  'use strict';
  const card = document.querySelector('#memberAuth, #authCard, body.auth-pending > .admin-board');
  if (!card) return;

  const background = document.createElement('div');
  background.className = 'auth-background';
  background.setAttribute('aria-hidden', 'true');
  document.body.insertBefore(background, document.body.firstChild);

  // Rendering stays in CSS; JavaScript only tracks login and page visibility.
  function sync() {
    background.hidden = card.hidden;
    background.classList.toggle('is-paused', document.hidden || card.hidden);
  }
  new MutationObserver(sync).observe(card, { attributes: true, attributeFilter: ['hidden'] });
  document.addEventListener('visibilitychange', sync);
  sync();
})();
