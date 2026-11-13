(() => {
  const labels = {name:'名称', count:'次数', dates:'日期时间', month_late:'本月累计迟到次数', group:'分组'};
  const kinds = {late:'迟到', absent:'旷班'};
  let draft, editing, previewTimer, revision = 0, dirty = false;
  const message = text => { $('pushStatus').textContent = text; };
  function changed() {
    dirty = true; message('有未保存的更改');
    clearTimeout(previewTimer); previewTimer = setTimeout(preview, 180);
  }
  function recipients() {
    $('pushRecipients').replaceChildren();
    if (!draft.recipients.length) $('pushRecipients').append(el('span', 'hint', '尚未添加收件邮箱'));
    draft.recipients.forEach(address => {
      const chip = el('span', 'push-recipient'); chip.append(el('span', '', address));
      const remove = button('', 'tonal icon-btn', () => { draft.recipients = draft.recipients.filter(a => a !== address); recipients(); changed(); });
      remove.dataset.icon = 'close'; remove.setAttribute('aria-label', `移除 ${address}`);
      chip.append(remove); $('pushRecipients').append(chip);
    });
  }
  function sections() {
    $('pushSections').replaceChildren();
    for (const [kind, name] of Object.entries(kinds)) {
      const config = draft.sections[kind], box = el('section', 'push-section'); box.dataset.kind = kind;
      const head = el('label', 'push-section-head'), toggle = el('input'); toggle.type = 'checkbox'; toggle.checked = config.enabled;
      const count = el('span', 'hint push-people'); count.id = `push-${kind}-people`;
      head.append(toggle, el('strong', '', name), count); box.append(head);
      const body = el('div', 'push-section-body'); body.hidden = !config.enabled;
      const cols = el('div', 'push-columns'); cols.setAttribute('aria-label', `${name}展示列和排序`);
      body.append(cols); box.append(body);
      const table = el('div', 'push-table-wrap'); table.id = `push-${kind}-table`; body.append(table);
      $('pushSections').append(box);
      toggle.onchange = () => { config.enabled = toggle.checked; body.hidden = !toggle.checked; changed(); };
      drawColumns(kind, cols);
    }
  }
  function drawColumns(kind, container) {
    const config = draft.sections[kind]; container.replaceChildren();
    config.columns = PushLayout.normalize(config.columns);
    for (const col of config.columns) {
      const pill = el('div', 'push-column' + (col.enabled ? '' : ' is-disabled'));
      pill.dataset.key = col.key; pill.tabIndex = 0; pill.setAttribute('role', 'button');
      pill.setAttribute('aria-label', `${labels[col.key]}，短按排序，长按拖动，Alt 加方向键调序`);
      pill.title = col.key === 'dates' ? '展示班次开始日期时间；按最早一次记录排序' : '短按排序 · 长按拖动';
      const check = el('input'); check.type = 'checkbox'; check.checked = col.enabled; check.setAttribute('aria-label', `展示${labels[col.key]}`);
      check.addEventListener('pointerdown', e => e.stopPropagation());
      check.addEventListener('click', e => e.stopPropagation());
      check.onchange = () => {
        if (config.enabled && !check.checked && config.columns.filter(c => c.enabled).length === 1) {
          check.checked = true; message('已启用的考勤项目至少需要展示一列'); return;
        }
        col.enabled = check.checked; drawColumns(kind, container); changed();
      };
      const arrow = config.sort.key === col.key ? (config.sort.direction === 'asc' ? ' ↑' : ' ↓') : '';
      pill.append(check, el('span', '', labels[col.key] + arrow)); container.append(pill);
      const sort = () => { config.sort = PushLayout.sort(config.sort, col.key); drawColumns(kind, container); changed(); };
      pill.onkeydown = e => {
        if (e.target !== pill) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sort(); }
        else if (e.altKey && ['ArrowLeft', 'ArrowRight'].includes(e.key)) {
          e.preventDefault(); const index = config.columns.indexOf(col), right = e.key === 'ArrowRight';
          const target = config.columns[index + (right ? 1 : -1)];
          if (target) config.columns = PushLayout.move(config.columns, col.key, target.key, right);
          drawColumns(kind, container); container.querySelector(`[data-key="${col.key}"]`).focus(); changed();
        }
      };
      let gesture;
      pill.onpointerdown = e => {
        if (e.button !== 0 || e.target === check || gesture) return;
        gesture = {at: performance.now(), x:e.clientX, y:e.clientY, moved:false, dragging:false};
        pill.setPointerCapture(e.pointerId);
        gesture.timer = setTimeout(() => {
          if (!gesture || gesture.moved) return;
          gesture.dragging = true; pill.classList.add('is-dragging');
        }, 350);
      };
      pill.onpointermove = e => {
        if (!gesture) return;
        if (Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) > 8) {
          gesture.moved = true; if (!gesture.dragging) clearTimeout(gesture.timer);
        }
        if (!gesture.dragging) return;
        const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('.push-column');
        if (!target || target.parentElement !== container || target === pill) return;
        const anchor = config.columns.find(c => c.key === target.dataset.key);
        if (anchor.enabled !== col.enabled) return;
        const rect = target.getBoundingClientRect(), after = e.clientX > rect.x + rect.width / 2;
        config.columns = PushLayout.move(config.columns, col.key, anchor.key, after);
        const next = after ? target.nextSibling : target;
        if (next !== pill && pill.nextSibling !== next) {
          container.insertBefore(pill, next);
          // Moving a captured node releases capture; renew it after reparenting.
          pill.setPointerCapture(e.pointerId);
        }
      };
      const finish = (e, cancelled = false) => {
        if (!gesture) return;
        clearTimeout(gesture.timer);
        const action = cancelled ? 'none' : PushLayout.release(performance.now() - gesture.at, gesture.moved, gesture.dragging);
        gesture = null; pill.classList.remove('is-dragging');
        if (pill.hasPointerCapture(e.pointerId)) pill.releasePointerCapture(e.pointerId);
        if (action === 'sort') sort();
        if (action === 'drag') { drawColumns(kind, container); changed(); }
      };
      pill.onpointerup = e => finish(e); pill.onpointercancel = e => finish(e, true);
      pill.onlostpointercapture = e => finish(e, true);
    }
  }
  async function preview() {
    if (!draft) return;
    const request = ++revision;
    $('pushPreviewStatus').textContent = '正在更新预览…';
    try {
      const data = await api('/api/admin/push/preview', 'POST', {config:draft, sample:$('pushSample').checked});
      if (request !== revision) return;
      $('pushTitle').textContent = data.title;
      $('pushOpening').textContent = data.opening || '（开头留空 · 双击编辑）';
      $('pushClosing').textContent = data.closing || '（结尾留空 · 双击编辑）';
      $('pushPreviewRange').textContent = `${data.start} — ${data.end}`;
      $('pushPreviewBadge').textContent = data.sample ? '示例数据 · 仅预览，不会发送' : '真实考勤 · 最近完整周期';
      for (const [kind, section] of Object.entries(data.sections)) {
        $(`push-${kind}-people`).textContent = section.enabled ? `${section.people} 人` : '未推送';
        const wrap = $(`push-${kind}-table`); wrap.replaceChildren();
        if (!section.rows.length) { wrap.append(el('p', 'hint push-empty', `本周期无${kinds[kind]}记录`)); continue; }
        const table = el('table', 'push-table'), thead = el('thead'), head = el('tr');
        for (const key of section.columns) { const th = el('th', '', labels[key]); th.scope = 'col'; head.append(th); }
        thead.append(head); table.append(thead); const tbody = el('tbody');
        section.rows.forEach(cells => { const row = el('tr'); cells.forEach(value => row.append(el('td', '', String(value)))); tbody.append(row); });
        table.append(tbody); wrap.append(table);
      }
      $('pushPreviewStatus').textContent = '';
    } catch (error) { if (request === revision) $('pushPreviewStatus').textContent = error.message; }
  }
  function drawStatus(data) {
    const config = data.config;
    $('pushNext').textContent = config.enabled && config.next_run
      ? `下次发送：${new Date(config.next_run).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false})}（北京时间）· 每 ${config.weeks} 周一次`
      : '定时推送未启用。首次启用后，在下一个可用的周日发送此前完整周期。';
    $('pushSmtp').textContent = data.smtp_ready ? 'SMTP 已启用，邮件使用「其他设置」中的发件邮箱。' : '启用前，请在「其他设置 → SMTP」配置并启用发件邮箱。';
    const history = $('pushDeliveries'); history.replaceChildren();
    if (!data.deliveries.length) history.append(el('p', 'hint', '暂无发送记录'));
    data.deliveries.forEach(row => {
      const item = el('div', 'push-delivery');
      item.append(el('span', '', row.recipient), el('span', 'hint', `截至 ${row.period_end}`),
        el('span', row.state === 'failed' ? 'push-error' : '', {sent:'已发送', failed:'失败 · 15 分钟后重试', sending:'发送中 / 待确认'}[row.state] || row.state));
      if (row.error) item.append(el('span', 'hint push-delivery-error', row.error));
      history.append(item);
    });
  }
  async function load() {
    message('正在加载…');
    try {
      const data = await api('/api/admin/push'); draft = data.config; dirty = false;
      $('pushEnabled').checked = draft.enabled; $('pushWeeks').value = draft.weeks; $('pushTime').value = draft.send_time;
      recipients(); sections(); drawStatus(data); message(''); await preview();
    } catch (error) { message(error.message); }
  }
  $('pushTab').onclick = () => { showPage('push'); if (!draft) load(); };
  $('pushRecipientForm').onsubmit = e => {
    e.preventDefault(); if (!draft) return;
    const input = $('pushAddress'), address = input.value.trim();
    if (draft.recipients.length >= 50) { message('最多添加 50 个目标邮箱'); return; }
    if (draft.recipients.some(value => value.toLowerCase() === address.toLowerCase())) { message('这个邮箱已添加'); return; }
    draft.recipients.push(address); input.value = ''; recipients(); changed(); input.focus();
  };
  $('pushEnabled').onchange = e => { if (draft) { draft.enabled = e.target.checked; changed(); } };
  $('pushWeeks').onchange = e => { if (draft) { draft.weeks = Number(e.target.value); changed(); } };
  $('pushTime').onchange = e => { if (draft) { draft.send_time = e.target.value; changed(); } };
  $('pushSample').onchange = preview;
  $('pushSave').onclick = async () => {
    if (!draft) return;
    if ($('pushAddress').value.trim()) { message('输入框内还有未添加的邮箱，请先按「添加邮箱」'); $('pushAddress').focus(); return; }
    const save = $('pushSave'); save.disabled = true; message('正在保存…');
    const snapshot = JSON.stringify(draft);
    try {
      const data = await api('/api/admin/push', 'PUT', draft);
      if (JSON.stringify(draft) === snapshot) { draft = data.config; dirty = false; message('已保存推送设置和邮件格式'); }
      else message('已保存；仍有新的未保存更改');
      drawStatus(data);
    } catch (error) { message(error.message); }
    finally { save.disabled = false; }
  };
  for (const [key, id, name] of [['title','pushTitle','标题'], ['opening','pushOpening','开头'], ['closing','pushClosing','结尾']]) {
    const open = () => {
      if (!draft) return; editing = key;
      $('pushEditorTitle').textContent = `编辑${name}`; $('pushEditorValue').value = draft[key];
      $('pushEditorValue').maxLength = key === 'title' ? 200 : 5000;
      $('pushEditorValue').rows = key === 'title' ? 2 : 6;
      $('pushEditorStatus').textContent = ''; $('pushEditor').showModal(); $('pushEditorValue').focus();
    };
    $(id).ondblclick = open;
    $(id).onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  }
  $('pushEditorClose').onclick = () => $('pushEditor').close();
  $('pushEditorForm').onsubmit = e => {
    e.preventDefault(); const value = $('pushEditorValue').value;
    if (editing === 'title' && (!value.trim() || /[\r\n]/.test(value))) { $('pushEditorStatus').textContent = '标题不能为空，也不能包含换行'; return; }
    draft[editing] = value; $('pushEditor').close(); changed();
  };
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
})();
