// Background refreshes never call the interaction endpoint.
function createIdleSession(options) {
  const {role, fetcher, now, setTimer, clearTimer, reload, hide, notify} = options;
  let authenticated = false, expiring = false, deadline = 0, offset = 0;
  let expiryTimer, activityTimer, syncing, sending = false, lastSent = 0, latestActivity = 0;
  const request = (path, init = {}) => fetcher(path, {...init, cache: 'no-store', signal: AbortSignal.timeout(8000)});
  async function expire() {
    if (expiring) return;
    expiring = true;
    clearTimer(expiryTimer); clearTimer(activityTimer);
    hide();
    try { await request('/api/' + role + '/logout', {method: 'POST'}); }
    catch (_) { /* Server-side lease has already expired even when offline. */ }
    finally { reload(); }
  }
  function sync() {
    if (expiring) return Promise.resolve();
    if (syncing) return syncing;
    syncing = (async () => {
      try {
        const response = await request('/api/session/idle?role=' + role);
        if (!response.ok) throw new Error('status');
        const state = await response.json();
        if (!state.logged_in) { if (authenticated) await expire(); return; }
        authenticated = true;
        offset = state.server_now * 1000 - now();
        deadline = state.expires_at * 1000 - offset;
        clearTimer(expiryTimer);
        expiryTimer = setTimer(sync, Math.max(0, deadline - now()));
      } catch (_) {
        if (authenticated && now() >= deadline) await expire();
      }
    })().finally(() => { syncing = null; });
    return syncing;
  }
  async function sendActivity() {
    clearTimer(activityTimer); activityTimer = null;
    if (!authenticated || expiring || sending) return;
    sending = true;
    lastSent = now();
    try {
      const response = await request('/api/session/activity?role=' + role, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({activity_at: latestActivity / 1000})
      });
      if (response.status === 401) await expire();
      else if (response.ok) { notify(); await sync(); }
    } catch (_) { /* Keep the existing deadline; network errors do not extend it. */ }
    finally { sending = false; }
  }
  function activity() {
    if (!authenticated || expiring) return;
    latestActivity = now() + offset;
    if (now() - lastSent >= 15000 || deadline - now() < 20000) sendActivity();
    else if (!activityTimer) activityTimer = setTimer(sendActivity, 15000 - (now() - lastSent));
  }
  function stop() {
    if (expiring) return;
    authenticated = false;
    clearTimer(expiryTimer); clearTimer(activityTimer);
  }
  return {sync, activity, stop, expire};
}
if (typeof module === 'object' && module.exports) module.exports = {createIdleSession};
if (typeof document !== 'undefined') (() => {
  const workspace = document.getElementById('workspace');
  if (!workspace) return;
  const role = document.body.dataset.admin === 'true' ? 'admin' : 'member';
  const key = 'gdutnic-idle-activity-' + role;
  const controller = createIdleSession({role, fetcher: window.fetch.bind(window), now: Date.now,
    setTimer: window.setTimeout.bind(window), clearTimer: window.clearTimeout.bind(window),
    reload: () => window.location.reload(), hide: () => { workspace.hidden = true; },
    notify: () => { try { localStorage.setItem(key, String(Date.now())); } catch (_) {} }
  });
  const sync = () => { if (!workspace.hidden) controller.sync(); };
  for (const type of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'scroll']) {
    document.addEventListener(type, event => { if (event.isTrusted && !workspace.hidden) controller.activity(); }, {capture: true, passive: true});
  }
  new MutationObserver(() => { if (workspace.hidden) controller.stop(); else sync(); }).observe(workspace, {attributes: true, attributeFilter: ['hidden']});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
  window.addEventListener('pageshow', sync);
  window.addEventListener('session-timeout-changed', sync);
  window.addEventListener('session-unresponsive', () => controller.expire());
  window.addEventListener('storage', event => { if (event.key === key) sync(); });
  setInterval(sync, 30000);
  sync();
})();
