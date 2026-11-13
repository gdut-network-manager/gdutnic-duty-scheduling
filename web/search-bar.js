// Material search bar: adds the trailing clear button to a .member-search box.
(() => {
  function attachSearchClear(box) {
    const input = box.querySelector('input');
    if (!input || box.querySelector('.search-clear')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'search-clear';
    button.dataset.icon = 'close';
    button.setAttribute('aria-label', '清除搜索');
    button.addEventListener('mousedown', event => event.preventDefault()); // keep focus in the field
    button.addEventListener('click', () => {
      if (!input.value) { input.blur(); button.blur(); return; }
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus({ preventScroll: true });
    });
    box.append(button);
  }
  window.attachSearchClear = attachSearchClear;
  document.querySelectorAll('.member-search').forEach(attachSearchClear);
})();
