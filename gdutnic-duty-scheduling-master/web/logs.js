// 操作日志：时间、操作账号、操作内容，支持按账号与关键字筛选。
async function loadLogs() {
  $('logsStatus').textContent = '正在加载日志…';
  const params = new URLSearchParams({limit: '300'});
  const actor = $('logActor').value;
  const query = $('logSearch').value.trim();
  if (actor) params.set('actor', actor);
  if (query) params.set('q', query);
  try {
    const data = await api('/api/admin/logs?' + params.toString());
    const rows = $('logRows');
    rows.replaceChildren();
    if (!data.items.length) {
      const tr = el('tr');
      const td = el('td', 'log-empty', '没有符合条件的日志');
      td.colSpan = 3;
      tr.append(td);
      rows.append(tr);
    }
    for (const item of data.items) {
      const tr = el('tr');
      const when = new Date(item.at);
      tr.append(
        el('td', 'log-time', Number.isNaN(when.getTime()) ? item.at
          : new Intl.DateTimeFormat('zh-CN', {
              timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
              hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
            }).format(when)),
        el('td', 'log-actor-cell', item.actor),
        el('td', 'log-detail', item.action + (item.detail ? '：' + item.detail : '')),
      );
      rows.append(tr);
    }
    // Keep the actor filter in sync without losing the current choice.
    const select = $('logActor');
    const current = select.value;
    select.replaceChildren(el('option', '', '全部账号'));
    select.options[0].value = '';
    for (const name of data.actors || []) {
      const option = el('option', '', name);
      option.value = name;
      select.append(option);
    }
    select.value = current;
    const mb = bytes => (bytes / 1024 / 1024).toFixed(2);
    const stats = data.stats;
    const usage = stats
      ? ` 存储 ${mb(stats.used_bytes)} MB / 上限 ${mb(stats.limit_bytes)} MB（共 ${stats.rows} 条；`
        + `超过上限后自动删除最旧记录至 ${mb(stats.keep_bytes)} MB）。`
      : '';
    $('logsStatus').textContent = `共 ${data.items.length} 条记录${query ? `，匹配“${query}”` : ''}。${usage}`;
  } catch (err) {
    $('logsStatus').textContent = err.message;
  }
}

$('logsTab').onclick = () => { showPage('logs'); loadLogs(); };
$('logRefresh').onclick = loadLogs;
$('logActor').onchange = loadLogs;
let logSearchTimer = 0;
$('logSearch').addEventListener('input', () => {
  clearTimeout(logSearchTimer);
  logSearchTimer = setTimeout(loadLogs, 250);
});
