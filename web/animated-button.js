// Shared animated button (arrow + expanding circle), used by 外勤登记/外勤确认/到岗签到.
// Requires `el()` from schedule.js and the `.animated-button` styles.
const ARROW_PATH = 'M16.1716 10.9999L10.8076 5.63589L12.2218 4.22168L20 11.9999L12.2218 19.778L10.8076 18.3638L16.1716 12.9999H4V10.9999H16.1716Z';

function animatedButton(label, hoverLabel, onPress, extraClass = '') {
  const btn = el('button', `animated-button${extraClass ? ' ' + extraClass : ''}`);
  btn.type = 'button';
  const arrow = cls => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', cls);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', ARROW_PATH);
    svg.append(path);
    return svg;
  };
  const text = el('span', 'text', label);
  btn.append(arrow('arr-2'), text, el('span', 'circle'), arrow('arr-1'));
  const locked = () => btn.classList.contains('registered') || btn.classList.contains('pending')
    || btn.disabled;
  btn.addEventListener('mouseenter', () => { if (!locked()) text.textContent = hoverLabel || label; });
  btn.addEventListener('mouseleave', () => { if (!locked()) text.textContent = label; });
  // Pointer presses also show the hover copy, since touch has no hover state.
  btn.addEventListener('pointerdown', () => { if (!locked()) text.textContent = hoverLabel || label; });
  btn.onclick = () => { if (!locked()) onPress(btn, text); };
  return {btn, text};
}

if (typeof window !== 'undefined') window.animatedButton = animatedButton;

// Wait for the actual completion transitions, including reduced-motion overrides.
async function playRegistrationAnimation(button, state = 'registered') {
  getComputedStyle(button).backgroundColor;
  button.classList.toggle('registered', state === 'registered');
  button.classList.toggle('pending', state === 'pending');
  await Promise.allSettled(button.getAnimations({subtree: true}).map(animation => animation.finished));
}

// Keeps the label in sync when the check-in window opens/closes.
function setAnimatedButtonState(btn, text, label, {disabled = false, registered = false} = {}) {
  btn.classList.toggle('registered', registered);
  btn.classList.remove('pending');
  btn.disabled = disabled;
  text.textContent = label;
  btn.setAttribute('aria-disabled', String(disabled));
  // Without a usable window the arrow should not suggest an action.
  btn.classList.toggle('is-locked', disabled && !registered);
}
