/* Column interactions and client-side email preview formatting. */
(function(root) {
  const shanghaiDate = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  });
  const dateText = date => date.toISOString().slice(0, 10);
  function addDays(date, days) {
    const result = new Date(date);
    result.setUTCDate(result.getUTCDate() + days);
    return result;
  }
  function compare(left, right) {
    if (typeof left === 'number') return left - right;
    // Match Python's code point order, including supplementary characters in pinyin keys.
    const remaining = right[Symbol.iterator]();
    for (const char of left) {
      const next = remaining.next();
      if (next.done) return 1;
      const difference = char.codePointAt(0) - next.value.codePointAt(0);
      if (difference) return difference;
    }
    return remaining.next().done ? 0 : -1;
  }
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
    release(duration, moved, dragging) { return dragging ? 'drag' : duration < 350 && !moved ? 'sort' : 'none'; },
    sample(weeks, now = new Date()) {
      const parts = Object.fromEntries(shanghaiDate.formatToParts(now).map(({type, value}) => [type, value]));
      const today = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
      const end = addDays(today, -today.getUTCDay() - 1);
      const start = addDays(end, 1 - weeks * 7);
      const rows = [
        {name:'示例成员甲', count:2, dates:[dateText(start) + ' 09:00', dateText(addDays(start, 2)) + ' 14:00'], month_late:3, group:'会员'},
        {name:'示例成员乙', count:1, dates:[dateText(end) + ' 09:00'], month_late:1, group:'外勤组'}
      ];
      return {start:dateText(start), end:dateText(end),
        sections:{late:rows, absent:rows},
        sort_keys:{示例成员甲:'shilichengyuanjia', 示例成员乙:'shilichengyuanyi', 会员:'huiyuan', 外勤组:'waiqinzu'}};
    },
    format(config, data) {
      const tokens = {'{开始日期}':data.start, '{结束日期}':data.end, '{周期周数}':String(config.weeks)};
      const text = key => config[key].replace(/\{(?:开始日期|结束日期|周期周数)\}/g, token => tokens[token]);
      const sections = {};
      for (const [kind, section] of Object.entries(config.sections)) {
        const columns = section.columns.filter(column => column.enabled).map(column => column.key);
        if (!section.enabled) {
          sections[kind] = {enabled:false, columns, rows:[], people:0};
          continue;
        }
        if (!columns.length) throw new Error(`${kind === 'late' ? '迟到' : '旷班'}至少需要启用一列`);
        const members = data.sections[kind].map(member => ({...member, dates:[...member.dates].sort()}));
        const pinyin = value => data.sort_keys[value];
        const {key, direction} = section.sort;
        const sortValue = member => {
          if (key === 'dates') return member.dates[0] || '';
          if (key === 'name' || key === 'group') return pinyin(member[key]);
          return member[key];
        };
        members.sort((left, right) => {
          const order = compare(sortValue(left), sortValue(right));
          return (direction === 'desc' ? -order : order) || compare(pinyin(left.name), pinyin(right.name));
        });
        const rows = [];
        const showDates = columns.includes('dates');
        for (const member of members) {
          const count = showDates ? member.dates.length : 1;
          for (let index = 0; index < count; index++) {
            rows.push(columns.map(column => column === 'dates' ? member.dates[index] : index === 0 ? member[column] : ''));
          }
        }
        sections[kind] = {enabled:true, columns, rows, people:members.length};
      }
      return {title:text('title'), opening:text('opening'), closing:text('closing'), sections};
    }
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = layout;
  else root.PushLayout = layout;
})(globalThis);
