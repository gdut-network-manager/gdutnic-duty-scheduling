/* Column behavior shared by pointer and keyboard interactions. */
(function(root) {
  const layout = {
    normalize(columns) { return [...columns.filter(c => c.enabled), ...columns.filter(c => !c.enabled)]; },
    sort(current, key) { return {key, direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc'}; },
    move(columns, source, target, after = false) {
      const moving = columns.find(c => c.key === source), anchor = columns.find(c => c.key === target);
      if (!moving || !anchor || moving === anchor || moving.enabled !== anchor.enabled) return columns;
      const result = columns.filter(c => c !== moving);
      result.splice(result.indexOf(anchor) + Number(after), 0, moving);
      return layout.normalize(result);
    },
    release(duration, moved, dragging) { return dragging ? 'drag' : duration < 350 && !moved ? 'sort' : 'none'; }
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = layout;
  else root.PushLayout = layout;
})(globalThis);
