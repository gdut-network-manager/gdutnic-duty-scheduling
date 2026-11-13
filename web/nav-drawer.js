// Mobile navigation drawer. On narrow screens the sidebar is off-canvas on the left;
// this adds the top bar that opens it and shows the current page. The sidebar markup and
// the page-switching code are untouched, so desktop behaves exactly as before.
(() => {
  const app = document.getElementById('workspace');
  const nav = app && app.querySelector('.ga-nav');
  const main = app && app.querySelector('.ga-main');
  if (!nav || !main) return;
  const narrow = matchMedia('(max-width: 800px)'); // keep in sync with schedule.css
  const make = (tag, className) => Object.assign(document.createElement(tag), { className });

  const bar = make('div', 'mobile-bar');
  const toggle = make('button', 'nav-toggle');
  const title = make('span', 'mobile-bar-title');
  const scrim = make('div', 'nav-scrim');
  nav.id ||= 'appNav';
  toggle.type = 'button';
  toggle.dataset.icon = 'menu';
  toggle.setAttribute('aria-controls', nav.id);
  scrim.setAttribute('aria-hidden', 'true');
  bar.append(toggle, title);
  main.prepend(bar);
  app.append(scrim);

  const isOpen = () => app.classList.contains('nav-open');
  const syncTitle = () => {
    const active = nav.querySelector('.ga-nav-item.active:not([hidden])');
    title.textContent = active ? active.textContent.trim() : '';
  };
  const setOpen = (open, { focus = false } = {}) => {
    open = open && narrow.matches;
    app.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '关闭导航菜单' : '打开导航菜单');
    main.inert = open; // keep keyboard focus inside the drawer while it covers the page
    if (!focus) return;
    if (open) (nav.querySelector('.ga-nav-item.active:not([hidden])') || nav.querySelector('.ga-nav-item:not([hidden])'))?.focus();
    else toggle.focus();
  };

  toggle.addEventListener('click', () => setOpen(!isOpen(), { focus: true }));
  scrim.addEventListener('click', () => setOpen(false));
  // Any action in the drawer closes it (a page, or a button that opens a dialog); only the
  // theme switch leaves it open, so the result of switching stays visible.
  nav.addEventListener('click', event => {
    if (event.target.closest('button') && !event.target.closest('.theme-toggle')) setOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && isOpen()) setOpen(false, { focus: true });
  });
  narrow.addEventListener('change', () => setOpen(false));
  new MutationObserver(syncTitle).observe(nav, { subtree: true, attributes: true, attributeFilter: ['class', 'hidden'] });
  syncTitle();
  setOpen(false);
})();
