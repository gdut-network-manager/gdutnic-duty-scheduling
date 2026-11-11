// Ripple feedback for every button, using the project palette.
// A ripple grows from the press point until it covers the farthest corner, then fades.
(() => {
  const RIPPLE_CLASS = 'ripple-ink';
  const SELECTOR = 'button, .btn, .ga-nav-item, .segment, .history-item, .md-select-option';

  function colorFor(node) {
    // Dark surfaces get a light ink and light surfaces a dark one, so the ripple stays visible.
    const style = getComputedStyle(node);
    const bg = style.backgroundColor;
    const dark = isDark(bg);
    return dark ? 'rgba(255, 255, 255, 0.38)' : 'rgba(24, 96, 120, 0.28)';
  }

  function isDark(value) {
    const match = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!match) return false;
    const parts = match[1].split(',').map(Number);
    if (parts.length > 3 && parts[3] === 0) return false; // transparent
    const [r, g, b] = parts;
    // Perceived luminance: light backgrounds need dark ink.
    return (0.299 * r + 0.587 * g + 0.114 * b) < 140;
  }

  function spawn(event) {
    const button = event.target.closest(SELECTOR);
    if (!button || button.disabled) return;
    // Skip elements that clip with their own radius logic or are not visible.
    const rect = button.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const style = getComputedStyle(button);
    if (style.position === 'static') button.style.position = 'relative';
    if (style.overflow !== 'hidden') button.style.overflow = 'hidden';

    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return;
    // Radius reaches the farthest corner from the press point.
    const radius = Math.max(
      Math.hypot(x, y),
      Math.hypot(rect.width - x, y),
      Math.hypot(x, rect.height - y),
      Math.hypot(rect.width - x, rect.height - y),
    );

    const ripple = document.createElement('span');
    ripple.className = RIPPLE_CLASS;
    ripple.style.width = ripple.style.height = `${radius * 2}px`;
    ripple.style.left = `${x - radius}px`;
    ripple.style.top = `${y - radius}px`;
    ripple.style.background = colorFor(button);
    button.appendChild(ripple);
    ripple.addEventListener('animationend', () => ripple.remove(), {once: true});
    // Safety net in case animationend never fires (reduced motion, tab hidden).
    setTimeout(() => ripple.remove(), 900);
  }

  document.addEventListener('pointerdown', spawn, {passive: true});
})();
