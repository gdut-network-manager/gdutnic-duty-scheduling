const ICON_FILES = {
  compose: "/icon/symbol/new_window_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
  close: "/icon/symbol/close_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
  send: "/icon/symbol/send_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
  stop: "/icon/symbol/stop_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
  user: "/icon/symbol/user_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
  server: "/icon/symbol/server_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
};

function mdIcon(name) {
  return `<span class="md-icon" style="--md-icon:url('${ICON_FILES[name]}')" aria-hidden="true"></span>`;
}

const ICON = {
  compose: mdIcon("compose"),
  close: mdIcon("close"),
  send: mdIcon("send"),
  stop: mdIcon("stop"),
};
const HISTORY_KEY = "gdutnic-chat-history";
const AUTH_KEY = "gdutnic-wiki-auth";

const els = {
  wikiGate: document.getElementById("wikiGate"),
  app: document.getElementById("app"),
  gateStatus: document.getElementById("gateStatus"),
  historyList: document.getElementById("historyList"),
  thread: document.getElementById("thread"),
  input: document.getElementById("input"),
  form: document.getElementById("composer"),
  composerDock: document.getElementById("composerDock"),
  btnOauth: document.getElementById("btnOauth"),
  btnNewSession: document.getElementById("btnNewSession"),
  btnLogout: document.getElementById("btnLogout"),
  btnSend: document.getElementById("btnSend"),
  btnAttach: document.getElementById("btnAttach"),
  imageInput: document.getElementById("imageInput"),
  imagePreviews: document.getElementById("imagePreviews"),
  appToast: document.getElementById("appToast"),
  btnMenu: document.getElementById("btnMenu"),
  drawerScrim: document.getElementById("drawerScrim"),
  chatBar: document.getElementById("chatBar"),
  chatTitle: document.getElementById("chatTitle"),
};

let messages = [];
let programmaticScroll = false;
let headerFadeTimer = 0;
let currentId = null;
let sessions = [];
const inflight = new Map();

function newId() {
  return crypto.randomUUID();
}

function loadAuth() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}

function saveAuth(data) {
  const access = (data?.access_token || "").trim();
  if (!access) return;
  localStorage.setItem(
    AUTH_KEY,
    JSON.stringify({
      access_token: access,
      refresh_token: data.refresh_token || "",
      mcp_url: data.mcp_url || "",
    })
  );
}

function authScope() {
  const token = loadAuth()?.access_token || "";
  let h = 0;
  for (let i = 0; i < token.length; i += 1) {
    h = (Math.imul(31, h) + token.charCodeAt(i)) | 0;
  }
  return String(h);
}

function historyStorageKey() {
  return `${HISTORY_KEY}:${authScope()}`;
}

const THEME_COOKIE = "gdutnic_themes";
let saveChatsTimer = 0;

function readThemeCookie() {
  const hit = document.cookie.split("; ").find((row) => xStarts(row, THEME_COOKIE + "="));
  if (!hit) return {};
  try {
    const parsed = JSON.parse(decodeURIComponent(hit.slice(THEME_COOKIE.length + 1)));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function xStarts(row, prefix) {
  return row.startsWith(prefix);
}

function writeThemeCookie(map) {
  document.cookie = `${THEME_COOKIE}=${encodeURIComponent(JSON.stringify(map))}; Path=/; Max-Age=${60 * 60 * 24 * 30}; SameSite=Lax`;
}

function themeIndex(theme) {
  const i = USER_THEMES.findIndex((t) => t.bg === theme.bg && t.ink === theme.ink);
  return i >= 0 ? i : 0;
}

function themeFromCookie(id) {
  const idx = readThemeCookie()[id];
  if (Number.isInteger(idx) && USER_THEMES[idx]) return { ...USER_THEMES[idx] };
  return null;
}

function rememberThemeCookie(id, theme) {
  if (!id || !theme) return;
  const map = readThemeCookie();
  map[id] = themeIndex(theme);
  writeThemeCookie(map);
}

async function loadHistoryStore() {
  try {
    const res = await fetch("/api/chats");
    const packed = await res.json();
    if (res.ok && Array.isArray(packed.sessions)) {
      sessions = packed.sessions.map(hydrateSession);
      currentId = packed.currentId || null;
      if (sessions.length) {
        retryUntitledSessions();
        return;
      }
    }
  } catch {
    /* fall through */
  }
  try {
    const scoped = JSON.parse(localStorage.getItem(historyStorageKey()) || "null");
    if (scoped && Array.isArray(scoped.sessions) && scoped.sessions.length) {
      sessions = scoped.sessions.map(hydrateSession);
      currentId = scoped.currentId || null;
      persistHistoryStore();
      retryUntitledSessions();
      return;
    }
    const legacy = JSON.parse(localStorage.getItem(HISTORY_KEY) || "{}");
    sessions = Array.isArray(legacy.sessions) ? legacy.sessions.map(hydrateSession) : [];
    currentId = legacy.currentId || null;
  } catch {
    sessions = [];
    currentId = null;
  }
}

function persistableSessions() {
  return sessions.filter((s) => s.committed && (s.messages || []).length);
}

function persistHistoryStore(immediate) {
  const packed = persistableSessions();
  packed.forEach((s) => {
    if (s.userTheme) rememberThemeCookie(s.id, s.userTheme);
  });
  const push = () =>
    fetch("/api/chats", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        currentId: packed.some((s) => s.id === currentId) ? currentId : packed[0]?.id || "",
        sessions: packed,
      }),
    }).catch(() => {});
  clearTimeout(saveChatsTimer);
  if (immediate === false) {
    saveChatsTimer = setTimeout(push, 280);
    return;
  }
  push();
}

function looksLikeCopiedReply(title) {
  const t = String(title || "").trim();
  if (!t || t === "新会话") return true;
  return /(我来|我帮您|根据回复|回复内容|生成一个|拟定|我们只需要|只输出|查询知识库)/.test(t);
}

function hydrateSession(raw) {
  const rec = raw && typeof raw === "object" ? raw : {};
  rec.committed = true;
  const title = String(rec.title || "").trim();
  rec.title = title || "新会话";
  rec.titleLocked = Boolean(title && title !== "新会话" && !looksLikeCopiedReply(title));
  return rec;
}

const USER_THEMES = [
  { bg: "#D1E4FF", ink: "#001D33" },
  { bg: "#DCF8C6", ink: "#102008" },
  { bg: "#FFDCC8", ink: "#311300" },
  { bg: "#E8DEF8", ink: "#1D192B" },
  { bg: "#E6E1E5", ink: "#1C1B1F" },
];

function pickUserTheme() {
  return USER_THEMES[Math.floor(Math.random() * USER_THEMES.length)];
}

function applyUserChatTheme(theme) {
  const t = theme && theme.bg && theme.ink ? theme : pickUserTheme();
  document.documentElement.style.setProperty("--user-bubble", t.bg);
  document.documentElement.style.setProperty("--user-ink", t.ink);
}

function sortSessions() {
  sessions.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}

function ensureSession(id) {
  let rec = sessions.find((s) => s.id === id);
  if (!rec) {
    rec = { id, title: "新会话", messages: [], updatedAt: 0, committed: false, titleLocked: false };
    sessions.push(rec);
  }
  if (!Array.isArray(rec.messages)) rec.messages = [];
  const fromCookie = themeFromCookie(id);
  if (fromCookie) {
    rec.userTheme = fromCookie;
  } else if (!rec.userTheme || !rec.userTheme.bg || !rec.userTheme.ink) {
    rec.userTheme = pickUserTheme();
  }
  rememberThemeCookie(id, rec.userTheme);
  return rec;
}

function bindCurrent(id) {
  const rec = ensureSession(id);
  currentId = id;
  messages = rec.messages;
  applyUserChatTheme(rec.userTheme);
  persistHistoryStore();
  syncHistoryActive();
  renderThreadFromMessages();
  setSending(inflight.has(id));
}

function syncHistoryActive() {
  if (!els.historyList) return;
  const items = els.historyList.querySelectorAll(".history-item");
  if (!items.length) {
    renderHistory();
    return;
  }
  items.forEach((el) => {
    el.classList.toggle("active", el.dataset.id === currentId);
  });
  updateChatTitle();
}

function markRound(id) {
  const rec = ensureSession(id);
  rec.updatedAt = Date.now();
  if (!rec.committed) return;
  sortSessions();
  persistHistoryStore();
  renderHistory();
}

function messagesForTitle(msgs) {
  return (msgs || [])
    .filter((m) => m.role === "assistant")
    .map((m) => ({ role: "assistant", content: messagePlain(m.content).trim().slice(0, 1200) }))
    .filter((m) => m.content)
    .slice(0, 2);
}

async function summarizeSessionTitle(id) {
  const rec = sessions.find((s) => s.id === id);
  if (!rec || rec.titleLocked) return;
  const round = messagesForTitle(rec.messages);
  if (!round.length) return;
  let title = "";
  try {
    const res = await fetch("/api/chat/title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: round }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) title = String(data.title || "").trim();
  } catch {
    title = "";
  }
  if (!title || looksLikeCopiedReply(title)) return;
  const latest = sessions.find((s) => s.id === id);
  if (!latest || latest.titleLocked) return;
  latest.title = title;
  latest.titleLocked = true;
  persistHistoryStore();
  renderHistory();
}

function retryUntitledSessions() {
  sessions
    .filter((s) => s.committed && !s.titleLocked)
    .forEach((s) => summarizeSessionTitle(s.id));
}

function abandonDraft(id) {
  if (!id) return;
  const ctrl = inflight.get(id);
  if (ctrl) ctrl.abort();
  inflight.delete(id);
  sessions = sessions.filter((s) => s.id !== id);
}

function isUnsavedDraft(id) {
  const rec = sessions.find((s) => s.id === id);
  if (!rec || rec.committed) return false;
  return Boolean((rec.messages || []).length || inflight.has(id));
}

function updateChatTitle() {
  if (!els.chatTitle) return;
  const rec = sessions.find((s) => s.id === currentId);
  els.chatTitle.textContent = rec?.title || "新会话";
}

function renderHistory() {
  if (!els.historyList) return;
  const saved = [...sessions]
    .filter((s) => s.committed && (s.messages || []).length)
    .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  if (!saved.length) {
    els.historyList.innerHTML = '<p class="history-empty">暂无历史会话</p>';
    updateChatTitle();
    return;
  }
  els.historyList.innerHTML = saved
    .map(
      (s) =>
        `<div class="history-item${s.id === currentId ? " active" : ""}" data-id="${s.id}">` +
        `<button type="button" class="history-open" data-id="${s.id}">${escapeHtml(s.title || "新会话")}</button>` +
        `<button type="button" class="history-del" data-del="${s.id}" title="删除" aria-label="删除">${ICON.close}</button>` +
        `</div>`
    )
    .join("");
  updateChatTitle();
}

function renderThreadFromMessages() {
  if (!els.thread) return;
  els.thread.innerHTML = "";
  for (const m of messages) {
    if (m.role !== "user" && m.role !== "assistant") continue;
    addMessage(m.role, m.content, "", { scroll: false });
  }
  if (inflight.has(currentId)) {
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant") {
      const thinkingChip = document.createElement("div");
      thinkingChip.className = "thinking-chip";
      thinkingChip.dataset.thinking = currentId;
      thinkingChip.innerHTML = '<span class="thinking-spinner" aria-hidden="true"></span><span>思考中...</span>';
      els.thread.appendChild(thinkingChip);
    }
  }
  scrollThreadToBottom();
  updateComposerChrome();
}

function isMobileLayout() {
  return window.matchMedia("(max-width: 900px)").matches;
}

function setDrawerOpen(open) {
  if (!els.app) return;
  els.app.classList.toggle("drawer-open", open);
  if (els.drawerScrim) els.drawerScrim.hidden = !open;
  if (els.btnMenu) els.btnMenu.setAttribute("aria-expanded", open ? "true" : "false");
}

function closeDrawer() {
  setDrawerOpen(false);
}

function startNewConversation() {
  if (isUnsavedDraft(currentId)) {
    abandonDraft(currentId);
    bindCurrent(newId());
    closeDrawer();
    return;
  }
  if (!messages.length && !inflight.has(currentId)) {
    if (!currentId) currentId = newId();
    persistHistoryStore();
    renderHistory();
    renderThreadFromMessages();
    closeDrawer();
    return;
  }
  bindCurrent(newId());
  closeDrawer();
}

function openSession(id) {
  if (!id || id === currentId) {
    closeDrawer();
    return;
  }
  if (isUnsavedDraft(currentId)) abandonDraft(currentId);
  bindCurrent(id);
  closeDrawer();
}

function deleteSession(id) {
  const ctrl = inflight.get(id);
  if (ctrl) ctrl.abort();
  inflight.delete(id);
  sessions = sessions.filter((s) => s.id !== id);
  if (currentId === id) bindCurrent(newId());
  else persistHistoryStore();
  renderHistory();
}

function setSending(on) {
  if (!els.btnSend) return;
  els.btnSend.classList.toggle("stop", on);
  els.btnSend.setAttribute("aria-label", on ? "中止" : "发送");
  if (on) {
    els.btnSend.innerHTML = ICON.stop;
  } else {
    els.btnSend.innerHTML = ICON.send;
  }
}

function scrollThreadToBottom() {
  programmaticScroll = true;
  els.thread.scrollTop = els.thread.scrollHeight;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      programmaticScroll = false;
    });
  });
}

function attachRipple() {
  document.addEventListener("pointerdown", (event) => {
    const capsule = event.target.closest(".history-item");
    const btn = capsule || event.target.closest(".btn");
    if (!btn || btn.classList.contains("send")) return;
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 2.2;
    const ripple = document.createElement("span");
    ripple.className = "ripple";
    ripple.style.width = `${size}px`;
    ripple.style.height = `${size}px`;
    ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
    ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
    btn.appendChild(ripple);
    ripple.addEventListener("animationend", () => ripple.remove());
  });
}

function setStatus(text, kind) {
  if (els.gateStatus && !document.body.classList.contains("wiki-ready")) {
    els.gateStatus.textContent = text;
    els.gateStatus.className = `hint status-text ${kind || ""}`;
  }
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function clearAuth() {
  localStorage.removeItem(AUTH_KEY);
}

async function logoutWiki() {
  try {
    await fetch("/api/mcp/logout", { method: "POST" });
  } catch {
    /* still leave locally */
  }
  clearAuth();
  inflight.forEach((ctrl) => ctrl.abort());
  inflight.clear();
  setSending(false);
  showGate();
  setStatus("", "idle");
}

function showGate() {
  document.body.classList.remove("wiki-ready");
  if (els.app) {
    els.app.hidden = true;
    els.app.classList.add("field-hidden");
  }
  if (els.wikiGate) {
    els.wikiGate.hidden = false;
    els.wikiGate.classList.remove("is-leaving");
  }
}

async function showApp() {
  if (els.wikiGate && !els.wikiGate.hidden) {
    els.wikiGate.classList.add("is-leaving");
    await wait(800);
    els.wikiGate.hidden = true;
    els.wikiGate.classList.remove("is-leaving");
  }
  document.body.classList.add("wiki-ready");
  if (els.app) {
    els.app.hidden = false;
    els.app.classList.remove("field-hidden");
  }
  updateComposerChrome();
}

const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const MAX_IMAGES = 4;
const COMPOSER_EXPAND_MS = 240;
const BUTTON_FADE_MS = 180;
const INPUT_MIN_H = 40;
const INPUT_MAX_H = 160;
let pendingImages = [];
let imageSeq = 0;
let toastTimer = 0;
let composerAnim = Promise.resolve();
let composerInputH = INPUT_MIN_H;
let inputSizerEl = null;

function messagePlain(content) {
  if (typeof content === "string") return content || "";
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && part.type === "text")
    .map((part) => part.text || "")
    .join("\n");
}

function hasMessageImage(content) {
  return Array.isArray(content) && content.some((part) => part && part.type === "image_url");
}

function fillUserBubble(bubble, content) {
  bubble.textContent = "";
  if (typeof content === "string" || !Array.isArray(content)) {
    bubble.textContent = messagePlain(content);
    return;
  }
  content.forEach((part) => {
    if (!part) return;
    if (part.type === "image_url") {
      const url = part.image_url?.url || part.url || "";
      if (!url) return;
      const img = document.createElement("img");
      img.className = "msg-image";
      img.src = url;
      img.alt = "上传的图片";
      bubble.appendChild(img);
    } else if (part.type === "text" && part.text) {
      const span = document.createElement("div");
      span.textContent = part.text;
      bubble.appendChild(span);
    }
  });
  if (!bubble.childNodes.length) bubble.textContent = "";
}

function showToast(text) {
  if (!els.appToast) return;
  els.appToast.textContent = text;
  els.appToast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.appToast.hidden = true;
  }, 2400);
}

function sniffImageType(bytes) {
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xd8) return "image/jpeg";
  if (bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return "image/png";
  if (bytes.length >= 6 && bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) return "image/gif";
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    return "image/webp";
  }
  return "";
}

function waitMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function queueComposerAnim(fn) {
  const run = composerAnim.then(fn, fn);
  composerAnim = run.catch(() => {});
  return run;
}

function applyInputHeight(px) {
  composerInputH = px;
  if (els.form) els.form.style.setProperty("--input-h", `${px}px`);
}

function setComposerHasImages(on) {
  if (els.form) els.form.classList.toggle("has-images", on);
}

function getInputSizer() {
  if (inputSizerEl) return inputSizerEl;
  inputSizerEl = document.createElement("div");
  inputSizerEl.className = "composer-sizer";
  inputSizerEl.setAttribute("aria-hidden", "true");
  document.body.appendChild(inputSizerEl);
  return inputSizerEl;
}

function measureInputHeight() {
  const el = els.input;
  if (!el) return INPUT_MIN_H;
  const sizer = getInputSizer();
  const cs = getComputedStyle(el);
  const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const linePx = parseFloat(cs.lineHeight) || 20;
  sizer.style.width = `${el.clientWidth}px`;
  sizer.style.fontFamily = cs.fontFamily;
  sizer.style.fontSize = cs.fontSize;
  sizer.style.fontWeight = cs.fontWeight;
  sizer.style.letterSpacing = cs.letterSpacing;
  sizer.style.lineHeight = cs.lineHeight;
  sizer.style.padding = cs.padding;
  sizer.style.border = "0";
  sizer.style.boxSizing = cs.boxSizing;
  sizer.style.whiteSpace = "pre-wrap";
  sizer.style.wordBreak = cs.wordBreak;
  sizer.style.overflowWrap = cs.overflowWrap;
  sizer.textContent = el.value.endsWith("\n") ? `${el.value}\n` : el.value || " ";
  const content = Math.max(linePx, sizer.scrollHeight - padY);
  const lines = Math.max(1, Math.round(content / linePx));
  return Math.min(INPUT_MAX_H, Math.max(INPUT_MIN_H, Math.round(padY + lines * linePx)));
}

async function syncComposerTextHeight() {
  const target = measureInputHeight();
  if (Math.abs(target - composerInputH) < 2) return;
  return queueComposerAnim(async () => {
    const next = measureInputHeight();
    if (Math.abs(next - composerInputH) < 2) return;
    await setComposerButtonsHidden(true);
    applyInputHeight(next);
    await waitMs(COMPOSER_EXPAND_MS);
    await setComposerButtonsHidden(false);
  });
}

async function setComposerButtonsHidden(hidden) {
  if (!els.form) return;
  els.form.classList.toggle("buttons-fading", hidden);
  await waitMs(BUTTON_FADE_MS);
}

function renderImagePreviews() {
  if (!els.imagePreviews) return;
  if (!pendingImages.length) {
    els.imagePreviews.innerHTML = "";
    return;
  }
  setComposerHasImages(true);
  els.imagePreviews.hidden = false;
  els.imagePreviews.innerHTML = pendingImages
    .map((item) => {
      const loading = item.loading || !item.dataUrl;
      const src = item.dataUrl ? ` src="${item.dataUrl}"` : "";
      return (
        `<div class="image-chip${loading ? " is-loading" : ""}" data-id="${item.id}">` +
        `<span class="chip-spin" aria-hidden="true"><span class="thinking-spinner"></span></span>` +
        `<img${src} alt="">` +
        `<span class="chip-mask"></span>` +
        `<button type="button" class="chip-del" data-remove="${item.id}" aria-label="移除图片">${ICON.close}</button>` +
        `</div>`
      );
    })
    .join("");
}

async function expandComposerForFirstImage() {
  return queueComposerAnim(async () => {
    if (!els.form || !els.imagePreviews) return;
    if (els.form.classList.contains("has-images")) return;
    await setComposerButtonsHidden(true);
    els.form.classList.add("has-images");
    els.imagePreviews.hidden = false;
    els.imagePreviews.innerHTML = "";
    await waitMs(COMPOSER_EXPAND_MS);
    await setComposerButtonsHidden(false);
  });
}

async function collapseComposerIfEmpty(opts) {
  const stayHidden = Boolean(opts && opts.stayHidden);
  const resetInput = Boolean(opts && opts.resetInput);
  return queueComposerAnim(async () => {
    if (pendingImages.length) renderImagePreviews();
    const hideImages = !pendingImages.length && Boolean(els.form && els.form.classList.contains("has-images"));
    const shrinkInput = resetInput && composerInputH > INPUT_MIN_H;
    if (!hideImages && !shrinkInput) {
      if (!pendingImages.length && els.imagePreviews) {
        els.imagePreviews.hidden = true;
        els.imagePreviews.innerHTML = "";
      }
      if (resetInput) applyInputHeight(INPUT_MIN_H);
      return false;
    }
    await setComposerButtonsHidden(true);
    if (hideImages) {
      setComposerHasImages(false);
      if (els.imagePreviews) {
        els.imagePreviews.hidden = true;
        els.imagePreviews.innerHTML = "";
      }
    }
    if (resetInput) applyInputHeight(INPUT_MIN_H);
    await waitMs(COMPOSER_EXPAND_MS);
    if (!stayHidden) await setComposerButtonsHidden(false);
    return stayHidden;
  });
}

function readBlobAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function blobToChatDataUrl(blob, mime, byteLength) {
  if (mime === "image/gif") return readBlobAsDataUrl(blob);
  const maxEdge = 1280;
  const skipResize = byteLength <= 700 * 1024;
  try {
    const bitmap = await createImageBitmap(blob);
    const needScale = Math.max(bitmap.width, bitmap.height) > maxEdge;
    if (!needScale && skipResize) {
      bitmap.close();
      return readBlobAsDataUrl(blob);
    }
    const scale = needScale ? maxEdge / Math.max(bitmap.width, bitmap.height) : 1;
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      bitmap.close();
      return readBlobAsDataUrl(blob);
    }
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close();
    const outType = mime === "image/png" ? "image/png" : "image/jpeg";
    const dataUrl = canvas.toDataURL(outType, outType === "image/jpeg" ? 0.82 : 0.92);
    return dataUrl || readBlobAsDataUrl(blob);
  } catch {
    return readBlobAsDataUrl(blob);
  }
}

async function addImageFile(file) {
  if (!file) return;
  if (pendingImages.length >= MAX_IMAGES) {
    showToast("一次最多 4 张图片");
    return;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    showToast("图片过大");
    return;
  }
  const firstImage = pendingImages.length === 0;
  if (firstImage) await expandComposerForFirstImage();
  const id = `img-${++imageSeq}`;
  pendingImages.push({ id, loading: true, dataUrl: "", mime: "" });
  renderImagePreviews();
  try {
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    const sniffed = sniffImageType(bytes);
    const mime = sniffed || (ALLOWED_IMAGE_TYPES.has(file.type) ? file.type : "");
    if (!mime || !ALLOWED_IMAGE_TYPES.has(mime)) {
      pendingImages = pendingImages.filter((item) => item.id !== id);
      await collapseComposerIfEmpty();
      showToast("图片不支持");
      return;
    }
    const blob = new Blob([buffer], { type: mime });
    const dataUrl = await blobToChatDataUrl(blob, mime, bytes.byteLength);
    const item = pendingImages.find((entry) => entry.id === id);
    if (!item) return;
    item.loading = false;
    item.dataUrl = dataUrl;
    item.mime = mime;
    renderImagePreviews();
  } catch {
    pendingImages = pendingImages.filter((item) => item.id !== id);
    await collapseComposerIfEmpty();
    showToast("图片不支持");
  }
}

function buildUserContent(text) {
  const ready = pendingImages.filter((item) => item.dataUrl && !item.loading);
  if (!ready.length) return text;
  const parts = ready.map((item) => ({
    type: "image_url",
    image_url: { url: item.dataUrl },
  }));
  if (text) parts.push({ type: "text", text });
  return parts;
}

function addMessage(role, content, extraClass, opts) {
  const bubble = document.createElement("div");
  bubble.className = `msg ${role}${extraClass ? " " + extraClass : ""}`;
  if (role === "assistant") {
    bubble.classList.add("markdown");
    bubble.innerHTML = renderMarkdown(messagePlain(content));
  } else if (role === "user") {
    fillUserBubble(bubble, content);
  } else {
    bubble.textContent = messagePlain(content);
  }
  if (role === "user" || role === "assistant") {
    const row = document.createElement("div");
    row.className = `msg-row ${role}`;
    const avatar = document.createElement("span");
    avatar.className = "msg-avatar";
    avatar.setAttribute("aria-hidden", "true");
    const icon = document.createElement("span");
    icon.className = "md-icon";
    icon.style.setProperty("--md-icon", `url('${ICON_FILES[role === "user" ? "user" : "server"]}')`);
    avatar.appendChild(icon);
    if (role === "user") {
      row.append(bubble, avatar);
    } else {
      row.append(avatar, bubble);
    }
    els.thread.appendChild(row);
  } else {
    els.thread.appendChild(bubble);
  }
  if (!opts || opts.scroll !== false) {
    scrollThreadToBottom();
    updateComposerChrome();
  }
  return bubble;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function errorText(data) {
  const d = data.detail || data.error || data.message;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  return d || "请求失败";
}

function payload() {
  const token = (loadAuth()?.access_token || "").trim();
  return token ? { mcp_api_key: token } : {};
}

async function restoreServerSession() {
  const auth = loadAuth();
  if (!auth?.access_token) return false;
  try {
    const res = await fetch("/api/mcp/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        outline_token: auth.access_token,
        outline_refresh: auth.refresh_token || "",
        mcp_url: auth.mcp_url || "",
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function applyLogin(data) {
  if (data?.access_token) saveAuth(data);
  await restoreServerSession();
  await loadHistoryStore();
  if (!currentId) currentId = newId();
  bindCurrent(currentId);
}

async function connect() {
  setStatus("正在通过本机网络连接 MCP…", "busy");
  try {
    const res = await fetch("/api/mcp/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(errorText(data));
    }
    setStatus("知识库已连接", "ok");
  } catch (err) {
    setStatus(err.message, "bad");
    throw err;
  }
}

async function sendChat(content, sessionId, signal) {
  const rec = ensureSession(sessionId);
  rec.messages.push({ role: "user", content });
  markRound(sessionId);
  if (currentId === sessionId) addMessage("user", content);

  let thinkingChip = null;
  if (currentId === sessionId) {
    thinkingChip = document.createElement("div");
    thinkingChip.className = "thinking-chip";
    thinkingChip.dataset.thinking = sessionId;
    thinkingChip.innerHTML = '<span class="thinking-spinner" aria-hidden="true"></span><span>思考中...</span>';
    els.thread.appendChild(thinkingChip);
    scrollThreadToBottom();
  }

  const snapshot = rec.messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({ role: m.role, content: m.content }));
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload(), messages: snapshot }),
    signal,
  });
  if (!res.ok || !res.body) {
    thinkingChip?.remove();
    const data = await res.json().catch(() => ({}));
    throw new Error(errorText(data));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";
  let assistant = null;

  const showAssistant = () => {
    if (currentId !== sessionId) return;
    const chip = els.thread.querySelector("[data-thinking]");
    if (chip) chip.remove();
    if (assistant && assistant.isConnected) return;
    assistant = addMessage("assistant", "");
    assistant.dataset.stream = sessionId;
  };

  const writeAssistant = (textValue) => {
    const row = sessions.find((s) => s.id === sessionId);
    if (!row) return;
    const last = row.messages[row.messages.length - 1];
    if (last && last.role === "assistant") last.content = textValue;
    else row.messages.push({ role: "assistant", content: textValue });
    if (currentId === sessionId) {
      showAssistant();
      if (assistant) assistant.innerHTML = renderMarkdown(textValue);
      scrollThreadToBottom();
    }
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (event.type === "thinking") {
          if (currentId === sessionId) {
            const chip = els.thread.querySelector("[data-thinking]");
            if (chip) chip.style.display = "flex";
          }
        } else if (event.type === "delta") {
          full += event.text;
          writeAssistant(full);
        } else if (event.type === "error") {
          throw new Error(event.message);
        }
      }
    }
  } catch (err) {
    if (full && sessions.some((s) => s.id === sessionId)) writeAssistant(full);
    throw err;
  }

  if (!sessions.some((s) => s.id === sessionId)) return;
  if (!full) {
    const fallback = "（模型没有返回文本）";
    writeAssistant(fallback);
  } else {
    writeAssistant(full);
  }
  const done = sessions.find((s) => s.id === sessionId);
  if (!done) return;
  done.committed = true;
  done.updatedAt = Date.now();
  sortSessions();
  persistHistoryStore();
  renderHistory();
  summarizeSessionTitle(sessionId);
}

function updateComposerChrome() {
  const thread = els.thread;
  if (!thread || !els.form) return;
  const scrolledUp = thread.scrollHeight - thread.scrollTop - thread.clientHeight > 32;
  if (els.composerDock) els.composerDock.classList.toggle("ghost", scrolledUp);
}

function onThreadScroll() {
  updateComposerChrome();
  if (programmaticScroll || !els.chatBar) return;
  els.chatBar.classList.add("dimmed");
  clearTimeout(headerFadeTimer);
  headerFadeTimer = setTimeout(() => {
    els.chatBar.classList.remove("dimmed");
  }, 500);
}

let oauthPending = false;
let oauthResetTimer = 0;
const OAUTH_LABEL = "网协认证登陆";

function setOauthLabel(text) {
  const span = els.btnOauth?.querySelector(".text");
  if (!span || span.textContent === text) return;
  span.style.opacity = "0";
  setTimeout(() => {
    if (!els.btnOauth) return;
    span.textContent = text;
    span.style.opacity = "1";
  }, 250);
}

function setOauthActive(on) {
  oauthPending = on;
  if (!els.btnOauth) return;
  const btn = els.btnOauth;
  if (!on && btn.classList.contains("is-error")) {
    btn.classList.add("is-leaving-error");
    btn.classList.remove("is-error", "is-active");
    setOauthLabel(OAUTH_LABEL);
    btn.setAttribute("aria-busy", "false");
    setTimeout(() => {
      btn.classList.remove("is-leaving-error");
    }, 800);
    return;
  }
  btn.classList.toggle("is-active", on);
  btn.classList.remove("is-error", "is-leaving-error");
  setOauthLabel(on ? "登陆中" : OAUTH_LABEL);
  btn.setAttribute("aria-busy", on ? "true" : "false");
}

function failOauthButton() {
  oauthPending = true;
  if (!els.btnOauth) {
    oauthPending = false;
    return;
  }
  els.btnOauth.classList.remove("is-leaving-error");
  els.btnOauth.classList.add("is-active", "is-error");
  setOauthLabel("登陆失败");
  els.btnOauth.setAttribute("aria-busy", "false");
  clearTimeout(oauthResetTimer);
  oauthResetTimer = setTimeout(() => {
    setOauthActive(false);
  }, 2500);
}

function startOauth() {
  if (oauthPending) return;
  clearTimeout(oauthResetTimer);
  setOauthActive(true);
  const popup = window.open(
    "/api/mcp/oauth/start",
    "outline-oauth",
    "width=520,height=740,menubar=no,toolbar=no,status=no"
  );
  if (!popup) {
    failOauthButton();
    setStatus("浏览器拦截了登录弹窗，请允许本站弹出窗口后重试", "bad");
    return;
  }
  setStatus("请在弹出窗口中用企业账号密码登录…", "busy");
  popup.focus();
  const timer = setInterval(() => {
    if (!popup.closed) return;
    clearInterval(timer);
    fetch("/api/defaults")
      .then((r) => r.json())
      .then((d) => {
        if (d.oauth_connected || d.has_outline_token || loadAuth()?.access_token) {
          setStatus("企业登录成功，正在连接知识库…", "ok");
          return applyLogin(d)
            .then(() => connect())
            .then(() => showApp())
            .catch((err) => {
              failOauthButton();
              setStatus(err.message || "连接知识库失败", "bad");
            });
        }
        if (!document.body.classList.contains("wiki-ready")) {
          failOauthButton();
          setStatus("登录未完成，请再试一次", "bad");
        }
      })
      .catch(() => {
        failOauthButton();
        setStatus("无法确认登录状态，请再试一次", "bad");
      });
  }, 600);
}

async function boot() {
  attachRipple();
  await restoreServerSession();
  await loadHistoryStore();
  if (!currentId) currentId = newId();
  bindCurrent(currentId);
  els.thread.addEventListener("scroll", onThreadScroll, { passive: true });
  updateComposerChrome();

  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    const data = event.data || {};
    if (data.type !== "outline-oauth") return;
    if (data.status === "ok") {
      setStatus("企业登录成功，正在连接知识库…", "ok");
      applyLogin(data)
        .then(() => connect())
        .then(() => showApp())
        .catch((err) => {
          failOauthButton();
          setStatus(err.message || "连接知识库失败", "bad");
        });
      return;
    }
    const map = {
      missing_url: "请先在管理员界面填写网协 MCP 地址",
      start: "无法打开企业登录：" + (data.message || ""),
      missing: "登录未完成",
      state: "登录校验失败，请再试一次",
      token: "登录换票失败：" + (data.message || ""),
    };
    failOauthButton();
    setStatus(map[data.status] || "登录未完成", "bad");
  });

  try {
    const res = await fetch("/api/defaults");
    const d = await res.json();
    if (!d.mcp_url) {
      showGate();
      setStatus("请先在管理员界面填写网协 MCP 地址", "idle");
    } else if (d.oauth_connected || d.has_outline_token || loadAuth()?.access_token) {
      try {
        await applyLogin(d);
        await connect();
        await showApp();
      } catch (err) {
        showGate();
        setOauthActive(false);
        setStatus(err.message || "知识库未连接，请重新登录", "bad");
      }
    } else {
      showGate();
    }
  } catch {
    showGate();
  }

  els.btnOauth.addEventListener("click", (e) => {
    e.preventDefault();
    startOauth();
  });

  els.btnNewSession.addEventListener("click", () => {
    startNewConversation();
  });

  if (els.btnLogout) {
    els.btnLogout.addEventListener("click", () => {
      logoutWiki();
    });
  }

  if (els.btnMenu) {
    els.btnMenu.addEventListener("click", () => {
      setDrawerOpen(!els.app.classList.contains("drawer-open"));
    });
  }
  if (els.drawerScrim) {
    els.drawerScrim.addEventListener("click", () => closeDrawer());
  }
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  window.addEventListener("resize", () => {
    if (!isMobileLayout()) closeDrawer();
  });

  els.historyList.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.preventDefault();
      e.stopPropagation();
      deleteSession(del.getAttribute("data-del"));
      return;
    }
    const item = e.target.closest(".history-item");
    if (item) openSession(item.getAttribute("data-id"));
  });

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentId) bindCurrent(newId());
    const sessionId = currentId;
    const running = inflight.get(sessionId);
    if (running) {
      running.abort();
      return;
    }
    const text = els.input.value.trim();
    if (pendingImages.some((item) => item.loading)) return;
    if (!text && !pendingImages.some((item) => item.dataUrl)) return;
    const content = buildUserContent(text);
    els.input.value = "";
    pendingImages = [];
    const keptHidden = await collapseComposerIfEmpty({ stayHidden: true, resetInput: true });
    const ctrl = new AbortController();
    inflight.set(sessionId, ctrl);
    setSending(true);
    if (keptHidden) await setComposerButtonsHidden(false);
    try {
      await sendChat(content, sessionId, ctrl.signal);
    } catch (err) {
      if (!sessions.some((s) => s.id === sessionId)) return;
      const rec = sessions.find((s) => s.id === sessionId);
      if (rec && !rec.committed) {
        rec.messages = (rec.messages || []).filter((m) => m.role !== "assistant");
      }
      if (currentId !== sessionId) return;
      if (err.name === "AbortError") {
        addMessage("error", "已中止当前回复", "error");
      } else {
        addMessage("error", err.message, "error");
      }
    } finally {
      inflight.delete(sessionId);
      if (currentId === sessionId) setSending(false);
    }
  });

  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      els.form.requestSubmit();
    }
  });
  els.input.addEventListener("input", () => {
    syncComposerTextHeight();
  });

  if (els.btnAttach && els.imageInput) {
    els.btnAttach.addEventListener("click", () => els.imageInput.click());
    els.imageInput.addEventListener("change", async () => {
      const files = [...(els.imageInput.files || [])];
      els.imageInput.value = "";
      for (const file of files) await addImageFile(file);
    });
  }
  if (els.imagePreviews) {
    els.imagePreviews.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-remove]");
      if (!btn) return;
      const id = btn.dataset.remove;
      pendingImages = pendingImages.filter((item) => item.id !== id);
      collapseComposerIfEmpty();
    });
  }
  els.form.addEventListener("paste", async (event) => {
    const files = [...(event.clipboardData?.items || [])]
      .map((item) => (item.kind === "file" ? item.getAsFile() : null))
      .filter(Boolean);
    if (!files.length) return;
    event.preventDefault();
    for (const file of files) await addImageFile(file);
  });
  els.form.addEventListener("dragover", (event) => {
    event.preventDefault();
  });
  els.form.addEventListener("drop", async (event) => {
    event.preventDefault();
    for (const file of [...(event.dataTransfer?.files || [])]) await addImageFile(file);
  });
}

boot();
