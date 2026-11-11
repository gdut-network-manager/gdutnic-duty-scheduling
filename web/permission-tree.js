// A shared, native disclosure tree. Only leaves are persisted as page permissions.
function buildPermissionTree(nodes, granted) {
  const tree = el('div', 'permission-tree');
  const held = new Set(granted || []);
  function branch(node) {
    const nested = Array.isArray(node.children);
    const wrapper = el(nested ? 'details' : 'div', nested ? 'permission-branch' : 'permission-leaf');
    const row = el(nested ? 'summary' : 'label', 'permission-option');
    const input = el('input'); input.type = 'checkbox';
    input.setAttribute('aria-label', node.label);
    const text = el('span'); text.append(el('strong', '', node.label));
    if (node.hint) row.title = node.hint;
    row.append(input, text); wrapper.append(row);
    if (nested) {
      input.dataset.branch = node.id;
      input.addEventListener('click', event => event.stopPropagation());
      const children = el('div', 'permission-children');
      for (const child of node.children) children.append(branch(child));
      wrapper.append(children);
      input.addEventListener('change', () => {
        for (const leaf of children.querySelectorAll('input[data-permission]')) leaf.checked = input.checked;
        refresh();
      });
    } else {
      input.dataset.permission = node.id;
      input.checked = held.has(node.id);
    }
    return wrapper;
  }
  function refresh() {
    for (const details of tree.querySelectorAll('.permission-branch')) {
      const parent = details.querySelector(':scope > summary input');
      const leaves = [...details.querySelectorAll('input[data-permission]')];
      parent.checked = leaves.length > 0 && leaves.every(input => input.checked);
      parent.indeterminate = !parent.checked && leaves.some(input => input.checked);
    }
  }
  for (const node of nodes) tree.append(branch(node));
  tree.addEventListener('change', refresh);
  refresh();
  return tree;
}
function readPermissionTree(tree) {
  return [...tree.querySelectorAll('input[data-permission]:checked')].map(input => input.dataset.permission);
}
