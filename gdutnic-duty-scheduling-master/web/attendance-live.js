// Heartbeats apply to the whole logged-in workspace; attendance long polls only
// run while a statistics/overview page is open. Neither counts as user activity.
function createAttendanceLive(options) {
  const {role, fetcher, now, setTimer, clearTimer, watching, onAttendance, onExpired, onForbidden} = options;
  let active = false, expired = false, revision = '', generation = 0;
  let heartbeatTimer, watchdog, pollTimer, controller;
  const request = (path, init, timeout) => fetcher(path, {...init, cache:'no-store', signal:AbortSignal.timeout(timeout)});
  function expire() {
    if (expired || !active) return;
    expired = true;
    stop();
    onExpired();
  }
  function armWatchdog(timeout = 90000) {
    clearTimer(watchdog);
    watchdog = setTimer(expire, timeout);
  }
  async function heartbeat(token) {
    try {
      const response = await request('/api/session/heartbeat?role=' + role, {method:'POST'}, 8000);
      if (!active || token !== generation) return;
      if (response.status === 401) { expire(); return; }
      if (response.ok) {
        const data = await response.json();
        if (active && token === generation) armWatchdog((data.timeout_seconds || 90) * 1000);
      }
    } catch (_) { /* The watchdog keeps the deadline from the last response. */ }
    if (active && token === generation) heartbeatTimer = setTimer(() => heartbeat(token), 20000);
  }
  let retryDelay = 2000;
  async function poll(token) {
    if (!active || token !== generation) return;
    // A timed-out long poll comes back with no revision change, so it re-arms at once
    // (retry 0). Any other failure backs off, otherwise every open tab hammers a broken
    // endpoint or a failing refresh every two seconds. A page that is simply not being
    // watched issues no request, so it keeps the short interval and resumes instantly.
    let retry = 2000, attempted = false, ok = false;
    if (watching()) {
      attempted = true;
      controller = new AbortController();
      const requestController = controller;
      const timeout = setTimer(() => requestController.abort(), 25000);
      try {
        const response = await fetcher('/api/attendance/live?role=' + role + '&after=' + encodeURIComponent(revision),
          {cache:'no-store', signal:requestController.signal});
        if (!active || token !== generation) return;
        if (response.status === 401) { expire(); return; }
        if (response.status === 403) { await onForbidden?.(); }
        else if (response.ok) {
          const data = await response.json();
          if (active && token === generation && data.revision !== revision) {
            // Read a fresh authorized snapshot, then patch the affected DOM nodes.
            if (watching()) {
              await onAttendance();
              revision = data.revision;
            }
          }
          retry = 0;
          ok = true;
        }
      } catch (_) { /* Retry after temporary disconnection without reloading the page. */ }
      finally {
        clearTimer(timeout);
        if (controller === requestController) controller = null;
      }
    }
    if (attempted) retryDelay = ok ? 2000 : Math.min(retryDelay * 2, 30000);
    if (retry && attempted && !ok) retry = retryDelay;
    if (active && token === generation) pollTimer = setTimer(() => poll(token), retry);
  }
  function start() {
    if (active) return;
    active = true; expired = false; revision = '';
    const token = ++generation;
    armWatchdog();
    heartbeat(token);
    poll(token);
  }
  function stop() {
    active = false; generation++;
    clearTimer(heartbeatTimer); clearTimer(watchdog); clearTimer(pollTimer);
    controller?.abort(); controller = null;
  }
  return {start, stop};
}
if (typeof module === 'object' && module.exports) module.exports = {createAttendanceLive};
if (typeof document !== 'undefined') (() => {
  const workspace = document.getElementById('workspace');
  if (!workspace) return;
  const admin = document.body.dataset.admin === 'true';
  const controller = createAttendanceLive({
    role:admin ? 'admin' : 'member', fetcher:window.fetch.bind(window), now:Date.now,
    setTimer:window.setTimeout.bind(window), clearTimer:window.clearTimeout.bind(window),
    watching:() => !workspace.hidden &&
      (admin ? !document.getElementById('dailyPage').hidden && window.adminPermissions?.includes('overview')
        : !document.getElementById('dutyPage').hidden),
    onAttendance:() => admin ? window.refreshOverviewAttendance?.() : window.refreshMemberAttendance?.(),
    onForbidden:() => admin ? window.refreshAdminPermissions?.() : undefined,
    onExpired:() => window.dispatchEvent(new Event('session-unresponsive')),
  });
  const sync = () => workspace.hidden ? controller.stop() : controller.start();
  new MutationObserver(sync).observe(workspace, {attributes:true, attributeFilter:['hidden']});
  window.addEventListener('pagehide', () => controller.stop());
  window.addEventListener('pageshow', sync);
  sync();
})();
