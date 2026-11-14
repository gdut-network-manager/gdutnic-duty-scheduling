const admin = typeof document !== 'undefined' && document.body.dataset.admin === 'true';
const $ = id => document.getElementById(id);
const dayInput = () => $('day') || {value: today()};
// The system clock (which admins may pin) is authoritative; browser time is the fallback.
let clockSkew = 0;
const clockNow = () => Date.now() + clockSkew;
window.clockNow = clockNow;
function syncClock(info) {
  if (!info || !info.current) return;
  const server = Date.parse(info.current);
  if (!Number.isFinite(server)) return;
  clockSkew = server - Date.now();
  // Let the timeline redraw immediately instead of waiting for its next rebuild tick.
  if (typeof window.refreshShiftTimeline === 'function') window.refreshShiftTimeline();
}
window.syncClock = syncClock;
const today = () => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(clockNow()));
let member = null;
let items = [], editing = null, editingSpecial = null, setup = true, generation = 0, rules = null;
async function api(url, method='GET', body) {
  const r = await fetch(url, {method, headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
  const data = await r.json();
  if (!r.ok) { const error=new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请检查填写内容'); error.status=r.status; throw error; }
  return data;
}
function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text !== undefined)n.textContent=text; return n; }
// 登录页用户名的两种状态：登录时必填，首次注册时可选（留空即用内置名称）。
function syncRootNameField(field, setup) {
  const label = field.closest('label');
  if (label) label.hidden = false;
  field.required = !!setup;
  field.placeholder = setup ? ' ' : '原始管理员';
  if (!setup) field.value = '';
}
if (typeof module === 'object' && module.exports) module.exports = {syncRootNameField};
// Semantic icons for card actions; CSS masks inherit each button's current colour.
const ACTION_ICONS = {
  '编辑班次': 'pencil-outline', '编辑': 'pencil-outline',
  '删除': 'delete-outline', '删除账号': 'delete-outline',
  '解绑SSO': 'link-off-outline',
  '更改权限': 'shield-account-outline', '更改密码': 'lock-outline',
  '更改学号': 'card-account-details-outline',
  '重置密码': 'lock-reset', '停用账号': 'account-off-outline',
  '启用账号': 'account-check-outline', '变更账号状态': 'account-off-outline',
};
function button(text, cls, fn) {
  const n=el('button','btn '+(cls==='delete'?'danger':cls==='secondary'?'tonal':cls||'primary'),text);
  if (ACTION_ICONS[text]) n.dataset.icon = ACTION_ICONS[text];
  n.type='button'; n.onclick=fn; return n;
}
async function load() {
  if (admin) {
    if (window.adminPermissions?.includes('overview') && $('dailyTab').classList.contains('active')
        && typeof window.loadOverview === 'function') await window.loadOverview();
    return;
  }
  const token=++generation;
  $('status').textContent='正在加载排班…';
  window.updateShiftTimeline([]);
  try {
    const data=await api('/api/member/schedules?day='+encodeURIComponent(dayInput().value));
    if(token !== generation)return;
    // Defence in depth for members even if an admin cookie is also present in the browser.
    const visible = admin ? data.items : data.items.filter(s => s.members.some(m => m.name === member?.username));
    syncClock(data.now);
    window.setNetworkAccess?.(data.network_access);
    items=visible; rules=data.rules; window.updateShiftTimeline(items); render(); $('status').textContent='';
  } catch(e) { if(token===generation){ $('shifts').replaceChildren(); $('status').textContent=e.message; if(!admin && e.status===401)await bootMember(); } }
}
function mountShiftCard(card) {
  const previous = $('shifts').querySelector(`[data-shift-id="${card.dataset.shiftId}"]`);
  if (previous) previous.replaceWith(card);
  else $('shifts').append(card);
}
function render(changedIds = null) {
  if (!changedIds) $('shifts').replaceChildren();
  if(!items.length){const box=el('div','empty');box.append(el('h2','', '当天暂无排班'),el('p','',admin?'点击「新增班次」开始安排值班。':'管理员发布排班后，会显示在这里。'));$('shifts').append(box);}
  for(const s of items){
    if (changedIds && !changedIds.has(s.id)) continue;
    const card=el('article','card'),top=el('div','shift-top'),info=el('div');
    card.dataset.shiftId = String(s.id);
    info.append(el('div','time',s.start+' — '+s.end));
    top.append(info,el('span','badge',`${s.members.filter(m=>m.checked_at).length} / ${s.members.length} 已签到`));card.append(top);
    if(s.note)card.append(el('p','',s.note));
    const members=el('div','members');
    for(const m of s.members){
      // Checked members always read "已签到"; only the colour reflects late/absent.
      const label = m.status === 'unconfirmed' ? '已签到（待确认）' : m.checked_at ? '已签到' : m.status==='absent' ? '旷班' : '待签到';
      const tone = m.status ? ' '+m.status : '';
      const n=el('span','member'+tone+(m.checked_at?' checked':''),m.name+' · '+label);
      if(m.checked_at)n.title=new Date(m.checked_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
      members.append(n);
    }card.append(members);
    if(admin){const actions=el('div','actions');actions.append(button('编辑班次','secondary',()=>openEditor(s)),button('删除','delete',async()=>{
      if(!confirm('确认删除此班次及其签到记录？'))return;
      try{await api('/api/admin/schedules/'+s.id,'DELETE');await load();}catch(e){$('status').textContent=e.message;}
    }));card.append(actions);}else{
      const own = s.members.find(m=>m.name === member?.username);
      if (!own) { card.append(el('p','','此班次未安排你值班。')); mountShiftCard(card); continue; }
      const form=el('form','check-form');
      // Attendance uses its own copy and green hover state.
      const {btn: submit, text: submitText} = window.animatedButton('确认签到', '确认签到',
        async (button, label) => {
          button.classList.add('is-submitting');
          button.disabled = true;
          try {
            await api(`/api/schedules/${s.id}/check-in`,'POST');
            label.textContent = '已签到';
            await playRegistrationAnimation(button);
            $('status').textContent = '';
            setTimeout(() => load(), 900);
          } catch (err) {
            button.classList.remove('registered');
            $('status').textContent = err.message;
            button.disabled = false;
            window.applyNetworkButton?.(button, 'checkin', label);
            if (err.status === 403) await window.refreshNetworkAccess?.();
          } finally {
            button.classList.remove('is-submitting');
          }
        }, 'check-in-button');
      if (own.checked_at) {
        submit.classList.add('registered');
        submit.disabled = true;
        submit.setAttribute('aria-disabled', 'true');
        submitText.textContent = '已签到';
        form.append(submit);
        card.append(form);
        mountShiftCard(card);
        continue;
      }
      // Mirror the server window: opens early_minutes before start and closes at end.
      const range=window.ShiftTimeline.interval(s);
      const window_ms={early:(rules?.early_minutes ?? 10)*60000, late:(rules?.late_minutes ?? 15)*60000, absent:(rules?.absent_minutes ?? 30)*60000};
      window.syncCheckInButton(submit, submitText, range, window_ms, clockNow());
      submit.classList.toggle('is-locked', submit.disabled);
      form.dataset.shiftId=String(s.id);
      form.append(submit);
      form.onsubmit = event => { event.preventDefault(); submit.click(); };
      card.append(form);
    }
    mountShiftCard(card);
  }
}
function openEditor(s){editing=s?s.id:null;editingSpecial=null;const f=$('shiftForm');f.reset();$('formStatus').textContent='';$('editorTitle').textContent=s?'编辑班次':'新增班次';for(const key of ['day','start','end','note'])f.elements[key].value=s?s[key]:(key==='day'?dayInput().value:'');f.elements.members.value=s?s.members.map(m=>m.name).join('\n'):'';$('editor').showModal();}
if($('day')){$('day').value=today();$('day').onchange=load;}if(admin){
  $('closeEditor').onclick=()=>$('editor').close();
  $('shiftForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('[type=submit]');const body=Object.fromEntries(new FormData(f));body.members=body.members.split(/[\n,，、]+/).map(n=>n.trim()).filter(Boolean);b.disabled=true;try{
  if(editingSpecial!==null){const saved=editingSpecial;await api('/api/admin/special-schedules'+(editingSpecial?'/'+editingSpecial:''),editingSpecial?'PUT':'POST',body);$('editor').close();await loadSpecials();$('specialStatus').textContent=saved?'特殊排班已更新。':'特殊排班已添加。';}
  else{await api('/api/admin/schedules'+(editing?'/'+editing:''),editing?'PUT':'POST',body);if($('day'))$('day').value=body.day;$('editor').close();await load();}
}catch(err){$('formStatus').textContent=err.message;}finally{b.disabled=false;}};
  $('authForm').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget;$('authStatus').textContent='';$('authSubmit').disabled=true;try{await api('/api/admin/'+(setup?'login':'register'),'POST',Object.fromEntries(new FormData(form)));form.reset();await boot();}catch(err){$('authStatus').textContent=err.message;}finally{$('authSubmit').disabled=false;}};
  $('logout').onclick=async()=>{try{await api('/api/admin/logout','POST');await boot();}catch(e){$('status').textContent=e.message;}};
  async function boot(){try{const state=await api('/api/admin/status');setup=state.setup;
    window.isRootAdmin = !!state.root;
    // 首次注册时可自定义原始管理员名称，留空则由服务端使用内置名称「原始管理员」。
    const userField = $('authForm').querySelector('[name=username]');
    if (userField) syncRootNameField(userField, setup);
    if ($('rootPasswordForm')) $('rootPasswordForm').hidden = !state.root;
    // Older backends do not send `permissions`; treat that as full access rather than hiding every menu.
    syncClock(state.now);
    window.adminPermissions = state.logged_in && Array.isArray(state.permissions) ? state.permissions : null;
    window.canEditAttendance = !Array.isArray(window.adminPermissions)
      || window.adminPermissions.includes('attendance_edit');
    window.canEditField = !Array.isArray(window.adminPermissions) || window.adminPermissions.includes('field');
    window.canRules = !Array.isArray(window.adminPermissions) || window.adminPermissions.includes('rules');
    window.canClock = !Array.isArray(window.adminPermissions) || window.adminPermissions.includes('clock');
    window.canSmtp = !Array.isArray(window.adminPermissions) || window.adminPermissions.includes('smtp');
    window.applySettingsPermissions?.();
    if (typeof window.applyPermissions === 'function') window.applyPermissions(window.adminPermissions);$('authTitle').textContent=setup?'管理员登录':'创建原始管理员';
    if ($('authHint')) $('authHint').textContent = setup
      ? '登录后维护班次与值班人员，查看签到记录。'
      : '首次创建「原始管理员」，拥有不可更改的最高权限；可自定义名称，留空则使用默认名称。';
    $('authSubmit').textContent=setup?'登录':'创建原始管理员';document.body.classList.toggle('auth-pending',!state.logged_in);$('authCard').hidden=state.logged_in;$('workspace').hidden=!state.logged_in;$('logout').hidden=!state.logged_in;if(state.logged_in)await load();}catch(e){$('authStatus').textContent=e.message;}}
  boot();
}else bootMember();
function updateCheckInButtons() {
  for(const form of document.querySelectorAll('#shifts .check-form')) {
    const button=form.querySelector('button'), shift=items.find(s=>String(s.id)===form.dataset.shiftId);
    const label=form.querySelector('.animated-button .text');
    if(button && label && shift && !button.classList.contains('registered') && !button.classList.contains('is-submitting')) {
      syncCheckInButton(button, label, window.ShiftTimeline.interval(shift), {
        early:(rules?.early_minutes ?? 10)*60000, late:(rules?.late_minutes ?? 15)*60000, absent:(rules?.absent_minutes ?? 30)*60000}, clockNow());
      if(!button.classList.contains('registered')) button.classList.toggle('is-locked', button.disabled);
    }
  }
}
if(!admin) setInterval(updateCheckInButtons, 1000);

// Time/rule changes in an already open member tab must reach its check-in controls.
let refreshingCheckinState = false;
async function refreshMemberCheckinState() {
  if (admin || !member || document.hidden || refreshingCheckinState) return;
  refreshingCheckinState = true;
  try {
    const state = await api('/api/member/status');
    if (!state.logged_in) { await bootMember(); return; }
    const previousDay = today();
    const previousAccess = window.networkAccess;
    const wasDutyHidden = $('dutyPage').hidden;
    const previousPermissions = [...(window.memberPermissions || [])].sort().join(',');
    const pageChanged = applyMemberPermissions(state.permissions || []);
    syncClock(state.now);
    rules = state.rules;
    window.setNetworkAccess?.(state.network_access);
    if (previousAccess && state.network_access &&
        (previousAccess.checkin !== state.network_access.checkin || previousAccess.field !== state.network_access.field)) {
      window.dispatchEvent(new Event('network-access-change'));
    }
    if (pageChanged && $('dutyPage').hidden) await loadCurrentMemberPage();
    if ((previousDay !== today() || wasDutyHidden) && !$('dutyPage').hidden) await load();
    else updateCheckInButtons();
    if (previousPermissions !== [...window.memberPermissions].sort().join(',') && !$('dutyPage').hidden && typeof loadFieldDuty === 'function') await loadFieldDuty();
  } catch (err) {
    if ($('status')) $('status').textContent = '无法同步签到时间，请刷新重试。';
  } finally { refreshingCheckinState = false; }
}
window.announceCheckinSettings = () => {
  try { localStorage.setItem('gdutnic_checkin_settings', String(Date.now())); } catch (_) {}
};
if (!admin) {
  setInterval(refreshMemberCheckinState, 15000);
  window.addEventListener('focus', refreshMemberCheckinState);
  document.addEventListener('visibilitychange', refreshMemberCheckinState);
  window.addEventListener('storage', event => {
    if (event.key === 'gdutnic_checkin_settings') refreshMemberCheckinState();
  });
}

function on(id, helper) {
  const node = $(id);
  if (node) node[helper[0]] = helper[1];
  return !!node;
}

async function bootMember() {
  try {
    const state = await api('/api/member/status');
    window.setNetworkAccess?.(state.network_access);
    rules = state.rules;
    member = state.logged_in ? state : null;
    document.body.classList.toggle('auth-pending', !member);
    if ($('memberAuth')) $('memberAuth').hidden = !!member;
    $('workspace').hidden = !member;
    if (member) {
      syncClock(state.now);
      if ($('memberGreeting')) $('memberGreeting').textContent = member.username+'，欢迎回来';
      applyMemberPermissions(state.permissions || []);
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadCurrentMemberPage, {once:true});
      else await loadCurrentMemberPage();
    }
    else { ++generation; items=[]; window.updateShiftTimeline([]); $('shifts').replaceChildren(); }
  } catch (err) { $('memberLoginStatus').textContent=err.message; }
}
if (!admin) {
  on('memberLoginForm', ['onsubmit', async e => {
    e.preventDefault(); const form=e.currentTarget, submit=form.querySelector('[type=submit]');
    submit.disabled=true; if ($('memberLoginStatus')) $('memberLoginStatus').textContent='';
    try { await api('/api/member/login','POST',Object.fromEntries(new FormData(form))); form.reset(); await bootMember(); }
    catch(err) { if ($('memberLoginStatus')) $('memberLoginStatus').textContent=err.message; }
    finally { submit.disabled=false; }
  }]);
  on('memberLogout', ['onclick', async () => {
    try { await api('/api/member/logout','POST'); await bootMember(); }
    catch(err) { if ($('status')) $('status').textContent=err.message; }
  }]);
  on('changePassword', ['onclick', () => {
    if (!$('passwordForm')) return;
    $('passwordForm').reset(); $('passwordStatus').textContent=''; $('passwordEditor').showModal();
  }]);
  on('closePassword', ['onclick', () => $('passwordEditor').close()]);
  $('passwordForm') && ($('passwordForm').onsubmit = async e => {
    e.preventDefault(); const form=e.currentTarget, submit=form.querySelector('[type=submit]'); submit.disabled=true;
    try { await api('/api/member/password','POST',Object.fromEntries(new FormData(form))); form.reset(); $('passwordEditor').close(); $('status').textContent='密码已更新。'; }
    catch(err) { $('passwordStatus').textContent=err.message; }
    finally { submit.disabled=false; }
  });
}

async function refreshAdminPermissions() {
  const state = await api('/api/admin/status');
  window.adminPermissions = state.logged_in ? (state.permissions || []) : [];
  window.canEditAttendance = window.adminPermissions.includes('attendance_edit');
  window.canEditField = window.adminPermissions.includes('field');
  window.canRules = window.adminPermissions.includes('rules');
  window.canClock = window.adminPermissions.includes('clock');
  window.canSmtp = window.adminPermissions.includes('smtp');
  if (window.applyPermissions) window.applyPermissions(window.adminPermissions);
  window.applySettingsPermissions?.();
  if (!state.logged_in) location.reload();
  return state;
}

function applyMemberPermissions(granted) {
  const held = new Set(granted);
  window.memberPermissions = held;
  const canOpen = needs => needs.some(permission => held.has(permission));
  // Sidebar order, top to bottom. The overview opens with either of its two actions;
  // without both, the first remaining page becomes the landing page.
  const pages = {duty: ['dutyTab', 'dutyPage', ['duty', 'member_field']],
    scheduleList: ['scheduleListTab', 'scheduleListPage', ['schedule_list']],
    confirm: ['fieldConfirmTab', 'fieldConfirmPage', ['field_confirm']]};
  const previous = Object.keys(pages).find(key => $(pages[key][0])?.classList.contains('active'));
  let selected = previous && canOpen(pages[previous][2]) ? previous : undefined;
  selected ||= Object.keys(pages).find(key => canOpen(pages[key][2]));
  for (const [key, [tab, page, permission]] of Object.entries(pages)) {
    if ($(tab)) { $(tab).hidden = !canOpen(permission); $(tab).classList.toggle('active', key === selected); }
    if ($(page)) $(page).hidden = key !== selected;
  }
  if ($('memberNoAccess')) $('memberNoAccess').hidden = !!selected;
  return selected !== previous;
}
async function loadCurrentMemberPage() {
  if ($('scheduleListTab')?.classList.contains('active') && typeof loadMemberSchedules === 'function') { await loadMemberSchedules(); return; }
  if ($('dutyTab')?.classList.contains('active')) {
    await load();
    if (typeof loadFieldDuty === 'function') await loadFieldDuty();
  }
  else if ($('fieldConfirmTab')?.classList.contains('active') && typeof loadFieldPending === 'function') await loadFieldPending();
}

if (!admin) window.addEventListener('network-access-change', () => { if (member && !$('dutyPage').hidden) render(); });


window.refreshMemberAttendance = async () => {
  if (admin || !member || $('dutyPage').hidden) return;
  const token = ++generation;
  const data = await api('/api/member/schedules?day=' + encodeURIComponent(dayInput().value));
  if (token !== generation || !member || $('dutyPage').hidden) return;
  const visible = data.items.filter(s => s.members.some(m => m.name === member.username));
  const before = new Map(items.map(s => [s.id, JSON.stringify(s)]));
  const changed = new Set(visible.filter(s => before.get(s.id) !== JSON.stringify(s)).map(s => s.id));
  const sameShifts = items.length === visible.length && visible.every(s => before.has(s.id));
  syncClock(data.now);
  window.setNetworkAccess?.(data.network_access);
  rules = data.rules; items = visible;
  if (!sameShifts) render();
  else if (changed.size) render(changed);
  window.updateShiftTimeline(items);
  if ($('status').textContent === '正在加载排班…') $('status').textContent = '';
};
