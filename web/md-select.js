// Material menu for every single-choice <select>. The native control stays in place and keeps
// its value, validation and label wiring; only the browser's popup list is replaced.
// A select with data-search="placeholder" gets a filter box at the top of its menu.
(() => {
  const make = (tag, className) => Object.assign(document.createElement(tag), { className });
  const menu = make('div', 'md-select-menu');
  const searchBox = make('div', 'md-select-search member-search');
  const search = make('input', '');
  const list = make('div', 'md-select-list');
  const empty = make('p', 'md-select-empty');
  searchBox.dataset.icon = 'magnify';
  search.type = 'search';
  search.autocomplete = 'off';
  search.setAttribute('role', 'combobox');
  search.setAttribute('aria-controls', 'mdSelectList');
  search.setAttribute('aria-expanded', 'true');
  searchBox.append(search);
  window.attachSearchClear(searchBox);
  list.id = 'mdSelectList';
  list.setAttribute('role', 'listbox');
  empty.textContent = '无匹配结果';
  menu.append(searchBox, list, empty);
  // The top layer keeps the menu above modal dialogs and outside any clipping ancestor.
  const topLayer = typeof menu.showPopover === 'function';
  if (topLayer) menu.popover = 'manual';
  let owner = null, active = -1, touchMoved = false, lastWidth = innerWidth;
  let motionGeneration = 0;

  const custom = (node) => (node instanceof HTMLSelectElement && !node.multiple && node.size <= 1 && !node.disabled ? node : null);
  const selectAt = (target) => custom(target instanceof Element ? target.closest('select') : null);
  const items = () => [...list.children];
  const usable = (item) => item && !item.hidden && item.getAttribute('aria-disabled') !== 'true';

  function setActive(index) {
    const all = items();
    active = all[index] ? index : -1;
    all.forEach((item, i) => item.classList.toggle('active', i === active));
    for (const node of [owner, search]) {
      if (active < 0) node.removeAttribute('aria-activedescendant');
      else node.setAttribute('aria-activedescendant', all[active].id);
    }
    if (active >= 0) all[active].scrollIntoView({ block: 'nearest' });
  }

  function step(delta, from = active) {
    const all = items();
    let index = from + delta;
    while (all[index] && !usable(all[index])) index += delta;
    if (all[index]) setActive(index);
  }

  function place() {
    const box = owner.getBoundingClientRect(), gap = 4, edge = 8;
    menu.style.maxHeight = '';
    const below = innerHeight - box.bottom - gap - edge, above = box.top - gap - edge;
    const up = below < Math.min(menu.offsetHeight, 200) && above > below;
    const heightLimit = menu.classList.contains('grouped') ? 410 : 320;
    menu.dataset.openUp = String(up);
    menu.style.maxHeight = `${Math.min(Math.max(up ? above : below, 120), heightLimit)}px`;
    menu.style.minWidth = `${box.width}px`;
    menu.style.left = `${Math.max(edge, Math.min(box.left, innerWidth - menu.offsetWidth - edge))}px`;
    menu.style.top = up ? '' : `${box.bottom + gap}px`;
    menu.style.bottom = up ? `${innerHeight - box.top + gap}px` : '';
    menu.style.transformOrigin = up ? 'bottom' : 'top';
  }

  function filter() {
    const query = search.value.trim().toLowerCase();
    let shown = 0;
    items().forEach((item, index) => {
      const option = owner.options[index];
      const match = !option.hidden && option.value !== ''
        && (!query || option.label.toLowerCase().includes(query));
      item.hidden = !match;
      if (match) shown += 1;
    });
    empty.hidden = shown > 0;
    setActive(-1);
    step(1, -1);
  }

  function open(select) {
    close();
    owner = select;
    menu.inert = false;
    list.replaceChildren(...[...select.options].map((option, index) => {
      const item = make('div', 'md-select-option');
      item.id = `mdSelectOption${index}`;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(option.selected));
      item.textContent = option.label;
      item.classList.toggle('selected', option.selected);
      if (option.disabled) item.setAttribute('aria-disabled', 'true');
      item.hidden = option.hidden || (select.dataset.search !== undefined && option.value === '');
      return item;
    }));
    const searchable = select.dataset.search !== undefined;
    menu.classList.toggle('grouped', searchable);
    searchBox.hidden = !searchable;
    empty.hidden = true;
    search.value = '';
    search.placeholder = select.dataset.search || '搜索';
    search.setAttribute('aria-label', search.placeholder);
    // Inside a modal dialog everything outside it is inert, so the menu must live in the dialog.
    (select.closest('dialog') || document.body).append(menu);
    menu.style.fontSize = getComputedStyle(select).fontSize;
    if (topLayer) menu.showPopover();
    place();
    select.setAttribute('aria-expanded', 'true');
    if (select.selectedIndex >= 0 && usable(items()[select.selectedIndex])) setActive(select.selectedIndex);
    else step(1, -1);
    const generation = ++motionGeneration;
    requestAnimationFrame(() => {
      if (generation !== motionGeneration || owner !== select) return;
      menu.classList.add('open');
      window.AppMotion?.menu(menu, true, menu.dataset.openUp === 'true');
    });
  }

  function close() {
    if (!owner) return;
    const select = owner, hadFocus = menu.contains(document.activeElement);
    select.removeAttribute('aria-expanded');
    select.removeAttribute('aria-activedescendant');
    owner = null;
    menu.inert = true;
    active = -1;
    const generation = ++motionGeneration;
    const animation = window.AppMotion?.menu(menu, false, menu.dataset.openUp === 'true');
    if (hadFocus) select.focus({ preventScroll: true });
    const remove = () => {
      if (generation !== motionGeneration || owner) return;
      menu.classList.remove('open');
      if (topLayer && menu.matches(':popover-open')) menu.hidePopover();
      menu.remove();
    };
    if (animation) animation.finished.catch(() => {}).then(remove);
    else remove();
  }

  function choose(index) {
    const select = owner, option = select.options[index];
    if (!option || option.disabled) return;
    close();
    if (select.selectedIndex === index) return;
    select.selectedIndex = index;
    select.dispatchEvent(new Event('input', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  document.addEventListener('mousedown', (event) => {
    if (menu.contains(event.target)) {
      if (event.target !== search) event.preventDefault(); // keep focus where it is
      return;
    }
    const select = event.button === 0 ? selectAt(event.target) : null;
    if (!select) { close(); return; }
    event.preventDefault(); // suppress the native popup
    if (select === owner) { close(); return; }
    select.focus({ preventScroll: true });
    open(select);
  }, true);

  // Touch browsers open the native picker before any mouse event, so claim the tap itself.
  document.addEventListener('touchstart', () => { touchMoved = false; }, { capture: true, passive: true });
  document.addEventListener('touchmove', () => { touchMoved = true; }, { capture: true, passive: true });
  document.addEventListener('touchend', (event) => {
    const select = selectAt(event.target);
    if (!select || touchMoved || !event.cancelable) return;
    event.preventDefault();
    if (select === owner) close(); else open(select);
  }, true);

  list.addEventListener('click', (event) => {
    const item = event.target.closest('.md-select-option');
    if (item) choose(items().indexOf(item));
  });
  search.addEventListener('input', filter);

  document.addEventListener('keydown', (event) => {
    const inSearch = owner && event.target === search;
    const select = inSearch ? owner : custom(event.target);
    if (!select || event.ctrlKey || event.metaKey) return;
    const key = event.key;
    if (select !== owner) {
      if (![' ', 'Enter', 'ArrowDown', 'ArrowUp'].includes(key)) return;
      event.preventDefault();
      open(select);
      return;
    }
    if (key === 'Tab') { close(); return; }
    // Keys typed into the search box edit the query; Enter may also be confirming an IME candidate.
    if (inSearch && (event.isComposing || event.keyCode === 229 || !['Escape', 'ArrowDown', 'ArrowUp', 'Enter'].includes(key))) return;
    event.preventDefault();
    event.stopPropagation(); // Escape closes the menu, not the surrounding dialog
    if (key === 'Escape') close();
    else if (key === 'ArrowDown') step(1);
    else if (key === 'ArrowUp') step(-1);
    else if (key === 'Home') step(1, -1);
    else if (key === 'End') step(-1, items().length);
    else if (key === 'Enter' || key === ' ') choose(active);
  }, true);

  document.addEventListener('focusout', (event) => {
    if (!owner || (event.target !== owner && event.target !== search)) return;
    if (event.relatedTarget !== owner && !menu.contains(event.relatedTarget)) close();
  });
  document.addEventListener('scroll', (event) => { if (!menu.contains(event.target)) close(); }, true);
  window.addEventListener('resize', () => {
    // An on-screen keyboard only changes the height; keep the menu open while typing.
    if (innerWidth !== lastWidth) close(); else if (owner) place();
    lastWidth = innerWidth;
  });
})();
