const PROVIDERS = [
  {
    id: "openai",
    name: "OpenAI",
    base: "https://api.openai.com/v1",
    keyHint: "sk-...",
    models: ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    hint: "官方 OpenAI Chat Completions。gpt-5.6 会路由到 gpt-5.6-sol。",
  },
  {
    id: "gemini",
    name: "Google Gemini",
    base: "https://generativelanguage.googleapis.com/v1beta/openai",
    keyHint: "AIza...",
    models: ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"],
    hint: "Gemini 的 OpenAI 兼容接口。",
  },
  {
    id: "grok",
    name: "xAI Grok",
    base: "https://api.x.ai/v1",
    keyHint: "xai-...",
    models: ["grok-4.6", "grok-4.5", "grok-4.3"],
    hint: "xAI 控制台申请的 Grok API Key。",
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    base: "https://api.deepseek.com",
    keyHint: "sk-...",
    models: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
    hint: "官方 OpenAI 兼容接口。发图时会自动改用 deepseek-v4-flash-vision-exp，仅支持 JPEG/PNG/GIF/WebP（按文件内容识别）。",
  },
  {
    id: "qwen",
    name: "通义千问",
    base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    keyHint: "sk-...",
    models: ["qwen3.7-plus", "qwen3.7-max", "qwen-plus", "qwen-flash", "qwen-max"],
    hint: "阿里云百炼 compatible-mode。",
  },
  {
    id: "moonshot",
    name: "Moonshot / Kimi",
    base: "https://api.moonshot.cn/v1",
    keyHint: "sk-...",
    models: ["kimi-k3", "kimi-k2.6", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"],
    hint: "Kimi 开放平台。kimi-k2 / moonshot-v1 已下线。",
  },
  {
    id: "zhipu",
    name: "智谱 GLM",
    base: "https://open.bigmodel.cn/api/paas/v4",
    keyHint: "API Key",
    models: ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-4.7", "glm-4.7-flash"],
    hint: "智谱开放平台，路径已含 v4。",
  },
  {
    id: "siliconflow",
    name: "硅基流动",
    base: "https://api.siliconflow.cn/v1",
    keyHint: "sk-...",
    models: [
      "deepseek-ai/DeepSeek-V4-Pro",
      "deepseek-ai/DeepSeek-V4-Flash",
      "Qwen/Qwen3.5-397B-A17B",
      "Qwen/Qwen3-32B",
    ],
    hint: "国内常用中转，模型名需带组织前缀。",
  },
  {
    id: "doubao",
    name: "火山方舟 Doubao",
    base: "https://ark.cn-beijing.volces.com/api/v3",
    keyHint: "方舟 API Key",
    models: ["doubao-seed-2.1-pro", "doubao-seed-2.1-turbo"],
    hint: "也可在厂商「自定义」中填写方舟接入点 ID（ep-...）。",
  },
  {
    id: "ollama",
    name: "Ollama（本地）",
    base: "http://127.0.0.1:11434/v1",
    keyHint: "可填 ollama",
    models: ["llama3.2", "qwen3", "deepseek-r1"],
    hint: "本机 Ollama，Key 可填任意非空值。需先 ollama pull 对应模型。",
  },
  {
    id: "custom",
    name: "自定义",
    base: "",
    keyHint: "sk-...",
    models: [],
    hint: "任意兼容 /chat/completions 的网关，自行填写 Base URL 和模型名。",
  },
];

const els = {
  authCard: document.getElementById("authCard"),
  authTitle: document.getElementById("authTitle"),
  authHint: document.getElementById("authHint"),
  authForm: document.getElementById("authForm"),
  authSubmit: document.getElementById("authSubmit"),
  authStatus: document.getElementById("authStatus"),
  adminUser: document.getElementById("adminUser"),
  adminPass: document.getElementById("adminPass"),
  workspace: document.getElementById("workspace"),
  accountWelcomeName: document.getElementById("accountWelcomeName"),
  adminList: document.getElementById("adminList"),
  adminDialogTitle: document.getElementById("adminDialogTitle"),
  mcpUrl: document.getElementById("mcpUrl"),
  mcpStatus: document.getElementById("mcpStatus"),
  btnSaveMcp: document.getElementById("btnSaveMcp"),
  btnMcpOauth: document.getElementById("btnMcpOauth"),
  btnMcpOauthLogout: document.getElementById("btnMcpOauthLogout"),
  mcpOauthStatus: document.getElementById("mcpOauthStatus"),
  qqAppId: document.getElementById("qqAppId"),
  qqAppSecret: document.getElementById("qqAppSecret"),
  qqEnabled: document.getElementById("qqEnabled"),
  qqStatus: document.getElementById("qqStatus"),
  btnSaveQq: document.getElementById("btnSaveQq"),
  btnQqOauth: document.getElementById("btnQqOauth"),
  btnQqOauthLogout: document.getElementById("btnQqOauthLogout"),
  qqOauthStatus: document.getElementById("qqOauthStatus"),
  mcpHeat: document.getElementById("mcpHeat"),
  mcpHeatValue: document.getElementById("mcpHeatValue"),
  mcpHeatHint: document.getElementById("mcpHeatHint"),
  provider: document.getElementById("provider"),
  openaiBase: document.getElementById("openaiBase"),
  openaiKey: document.getElementById("openaiKey"),
  openaiModelSelect: document.getElementById("openaiModelSelect"),
  openaiModel: document.getElementById("openaiModel"),
  modelSelectWrap: document.getElementById("modelSelectWrap"),
  customModelWrap: document.getElementById("customModelWrap"),
  providerHint: document.getElementById("providerHint"),
  btnSaveModel: document.getElementById("btnSaveModel"),
  btnSavePrompt: document.getElementById("btnSavePrompt"),
  btnLogout: document.getElementById("btnLogout"),
  modelStatus: document.getElementById("modelStatus"),
  promptStatus: document.getElementById("promptStatus"),
  systemPrompt: document.getElementById("systemPrompt"),
  btnAddAdmin: document.getElementById("btnAddAdmin"),
  adminDialog: document.getElementById("adminDialog"),
  adminDialogForm: document.getElementById("adminDialogForm"),
  adminDialogBackdrop: document.getElementById("adminDialogBackdrop"),
  adminDialogStatus: document.getElementById("adminDialogStatus"),
  newAdminUser: document.getElementById("newAdminUser"),
  newAdminPass: document.getElementById("newAdminPass"),
  btnDialogCancel: document.getElementById("btnDialogCancel"),
  btnDialogDelete: document.getElementById("btnDialogDelete"),
};

let keysByProvider = {};
let authMode = "register";
let selectsReady = false;
let lastProvider = "openai";
let currentUsername = "";
let dialogMode = "add";
let editingUsername = "";

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function plainContent(content) {
  if (typeof content === "string") return content || "";
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && part.type === "text")
    .map((part) => part.text || "")
    .join("\n");
}

function userMessageHtml(content) {
  if (typeof content === "string" || !Array.isArray(content)) return escapeHtml(plainContent(content));
  return content
    .map((part) => {
      if (!part) return "";
      if (part.type === "image_url") {
        const url = part.image_url?.url || "";
        if (!url.startsWith("data:image/")) return "";
        return `<img class="msg-image" src="${escapeHtml(url)}" alt="">`;
      }
      if (part.type === "text") return `<div>${escapeHtml(part.text || "")}</div>`;
      return "";
    })
    .join("");
}

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function applyCacheStats(data) {
  const input = document.getElementById("chatCacheMb");
  if (input && document.activeElement !== input) {
    input.value = String(data.max_mb || 32);
  }
}

function bindCacheLimit() {
  const input = document.getElementById("chatCacheMb");
  if (!input || input.dataset.bound === "1") return;
  input.dataset.bound = "1";
  input.addEventListener("change", () => saveCacheLimit());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveCacheLimit();
    }
  });
}

async function saveCacheLimit() {
  const input = document.getElementById("chatCacheMb");
  const thread = document.getElementById("chatRecordThread");
  const maxMb = Number(input && input.value);
  if (!Number.isFinite(maxMb) || maxMb < 1) return;
  try {
    const res = await fetch("/api/admin/chats/cache", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_mb: maxMb }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(errorText(data));
    applyCacheStats(data);
    renderSessionGroups(data.items || []);
    if (thread) thread.innerHTML = "";
  } catch (err) {
    console.warn(err);
  }
}

function renderSessionGroups(items) {
  const list = document.getElementById("sessionList");
  if (!list) return;
  if (!items.length) {
    list.innerHTML = '<p class="history-empty">暂无对话缓存</p>';
    return;
  }
  const groups = new Map();
  items.forEach((item) => {
    const key = item.visitor_id || "unknown";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  const cards = [...groups.entries()].sort((a, b) => {
    const ta = Math.max(...a[1].map((x) => x.updatedAt || 0));
    const tb = Math.max(...b[1].map((x) => x.updatedAt || 0));
    return tb - ta;
  });
  list.innerHTML = cards
    .map(([visitorId, chats]) => {
      const name = abbreviateSession(visitorId);
      const rows = chats
        .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
        .map(
          (item) =>
            `<button type="button" class="session-chat-item" data-visitor="${escapeHtml(visitorId)}" data-id="${escapeHtml(item.id)}">` +
            `${escapeHtml(item.title || "新会话")}` +
            `<small>${formatTime(item.updatedAt)} · ${item.message_count || 0} 条</small>` +
            `</button>`
        )
        .join("");
      return `<article class="session-card"><h3>会话 ${escapeHtml(name)}</h3><div class="session-chats">${rows}</div></article>`;
    })
    .join("");
  list.querySelectorAll(".session-chat-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      list.querySelectorAll(".session-chat-item").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      openChatRecord(btn.dataset.visitor, btn.dataset.id);
    });
  });
}

async function loadChatRecords() {
  const list = document.getElementById("sessionList");
  const thread = document.getElementById("chatRecordThread");
  bindCacheLimit();
  if (!list || !thread) return;
  list.innerHTML = '<p class="history-empty">正在加载…</p>';
  thread.innerHTML = "";
  try {
    const res = await fetch("/api/admin/chats");
    const data = await res.json();
    if (!res.ok) throw new Error(errorText(data));
    applyCacheStats(data);
    renderSessionGroups(data.items || []);
  } catch (err) {
    list.innerHTML = `<p class="history-empty">${escapeHtml(err.message)}</p>`;
  }
}

function abbreviateSession(id) {
  const s = String(id || "");
  if (s.length <= 8) return s;
  return `${s.slice(0, 4)}…${s.slice(-3)}`;
}

async function openChatRecord(visitorId, chatId) {
  const thread = document.getElementById("chatRecordThread");
  if (!thread) return;
  thread.innerHTML = '<p class="history-empty">正在加载…</p>';
  try {
    const res = await fetch(`/api/admin/chats/${encodeURIComponent(visitorId)}/${encodeURIComponent(chatId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(errorText(data));
    const theme = data.userTheme || {};
    if (theme.bg && theme.ink) {
      thread.style.setProperty("--user-bubble", theme.bg);
      thread.style.setProperty("--user-ink", theme.ink);
    }
    const userIcon = "/icon/symbol/user_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg";
    const serverIcon = "/icon/symbol/server_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg";
    const rows = (data.messages || [])
      .map((m) => {
        const isUser = m.role === "user";
        const icon = isUser ? userIcon : serverIcon;
        const bubble = isUser
          ? `<div class="msg user">${userMessageHtml(m.content)}</div>`
          : `<div class="msg assistant markdown">${renderMarkdown(plainContent(m.content))}</div>`;
        const avatar = `<span class="msg-avatar" aria-hidden="true"><span class="md-icon" style="--md-icon:url('${icon}')"></span></span>`;
        return isUser
          ? `<div class="msg-row user">${bubble}${avatar}</div>`
          : `<div class="msg-row assistant">${avatar}${bubble}</div>`;
      })
      .join("");
    thread.innerHTML = rows || '<p class="history-empty">这条对话还没有内容</p>';
  } catch (err) {
    thread.innerHTML = `<p class="history-empty">${escapeHtml(err.message)}</p>`;
  }
}

function errorText(data) {
  const d = data.detail || data.error || data.message;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  return d || "请求失败";
}

function setCurrentUsername(username) {
  currentUsername = (username || "").trim();
  if (els.accountWelcomeName) {
    els.accountWelcomeName.textContent = currentUsername;
  }
}

function renderAdminList(names) {
  if (!els.adminList) return;
  els.adminList.replaceChildren();
  (names || []).forEach((name) => {
    const isSelf = name === currentUsername;
    const card = document.createElement("div");
    card.className = "ga-card ga-admin-card" + (isSelf ? " is-self" : "");
    const label = document.createElement("p");
    label.className = "ga-username";
    label.textContent = name;
    card.appendChild(label);
    if (!isSelf) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn tonal";
      btn.textContent = "编辑";
      btn.addEventListener("click", () => openAdminDialog("edit", name));
      card.appendChild(btn);
    }
    els.adminList.appendChild(card);
  });
}

async function loadAdmins() {
  const res = await fetch("/api/admin/users");
  const data = await res.json();
  if (!res.ok) throw new Error(errorText(data));
  renderAdminList(data.usernames || []);
  if (!currentUsername && data.usernames && data.usernames[0]) {
    setCurrentUsername(data.usernames[0]);
  }
}

function currentProvider() {
  return PROVIDERS.find((p) => p.id === els.provider.value) || PROVIDERS[0];
}

function selectedModel() {
  if (currentProvider().id === "custom") return els.openaiModel.value.trim();
  return els.openaiModelSelect.value;
}

function enhanceSelect(select) {
  if (select.dataset.enhanced === "1") {
    refreshSelectMenu(select);
    return;
  }
  select.dataset.enhanced = "1";
  const wrap = document.createElement("div");
  wrap.className = "md-select";
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  select.classList.add("md-select-native");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "md-select-btn";
  const menu = document.createElement("div");
  menu.className = "md-select-menu";
  wrap.append(btn, menu);
  select._mdWrap = wrap;
  select._mdBtn = btn;
  select._mdMenu = menu;
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    document.querySelectorAll(".md-select.open").forEach((el) => {
      if (el !== wrap) el.classList.remove("open");
    });
    wrap.classList.toggle("open");
  });
  menu.addEventListener("click", (event) => {
    const opt = event.target.closest(".md-select-option");
    if (!opt) return;
    select.value = opt.dataset.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    wrap.classList.remove("open");
    refreshSelectMenu(select);
  });
  refreshSelectMenu(select);
}

function refreshSelectMenu(select) {
  if (!select._mdBtn) return;
  const current = select.options[select.selectedIndex];
  select._mdBtn.textContent = current ? current.text : "";
  select._mdMenu.innerHTML = [...select.options]
    .map(
      (opt) =>
        `<button type="button" class="md-select-option${opt.selected ? " selected" : ""}" data-value="${opt.value}">${opt.text}</button>`
    )
    .join("");
}

function fillProviderSelect() {
  els.provider.innerHTML = PROVIDERS.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
  enhanceSelect(els.provider);
}

function fillModelSelect(provider, savedModel) {
  const models = provider.models || [];
  els.openaiModelSelect.innerHTML = models.map((m) => `<option value="${m}">${m}</option>`).join("");
  if (savedModel && models.includes(savedModel)) {
    els.openaiModelSelect.value = savedModel;
  } else if (models.length) {
    els.openaiModelSelect.value = models[0];
  }
  syncModelFields();
  refreshSelectMenu(els.openaiModelSelect);
}

function syncModelFields() {
  const custom = currentProvider().id === "custom";
  els.modelSelectWrap.classList.toggle("field-hidden", custom);
  els.modelSelectWrap.hidden = custom;
  els.customModelWrap.classList.toggle("field-hidden", !custom);
  els.customModelWrap.hidden = !custom;
}

function applyProvider(provider, { keepBase = false, savedModel = "" } = {}) {
  els.providerHint.textContent = provider.hint;
  els.openaiKey.placeholder = provider.keyHint;
  if (provider.id === "custom") {
    if (!keepBase) els.openaiBase.value = "";
    els.openaiBase.placeholder = "https://your-gateway/v1";
  } else if (!keepBase && provider.base) {
    els.openaiBase.value = provider.base;
    els.openaiBase.placeholder = "由厂商自动填入，可改";
  }
  fillModelSelect(provider, savedModel);
  enhanceSelect(els.provider);
  enhanceSelect(els.openaiModelSelect);
  refreshSelectMenu(els.provider);
  refreshSelectMenu(els.openaiModelSelect);
  syncModelFields();
  const storedKey = keysByProvider[provider.id];
  if (typeof storedKey === "string") els.openaiKey.value = storedKey;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function openAdminDialog(mode = "add", username = "") {
  dialogMode = mode;
  editingUsername = username || "";
  els.adminDialogStatus.textContent = "";
  els.adminDialogTitle.textContent = mode === "edit" ? "编辑管理员" : "注册管理员";
  els.newAdminUser.value = mode === "edit" ? username : "";
  els.newAdminPass.value = "";
  if (els.btnDialogDelete) {
    els.btnDialogDelete.hidden = mode !== "edit";
  }
  els.adminDialog.hidden = false;
  await wait(20);
  els.adminDialog.classList.add("is-open");
  els.newAdminUser.focus();
}

async function closeAdminDialog() {
  els.adminDialog.classList.remove("is-open");
  await wait(220);
  els.adminDialog.hidden = true;
}

async function showAuth(mode) {
  authMode = mode;
  els.authTitle.textContent = mode === "register" ? "注册管理员" : "管理员登录";
  els.authHint.textContent = "请输入用户名和密码";
  els.authSubmit.textContent = mode === "register" ? "注册" : "登录";
  const ssoButton = document.querySelector('[data-keycloak-login="admin"]');
  if (ssoButton) ssoButton.hidden = mode === "register" || ssoButton.dataset.ssoEnabled !== "true";
  els.adminPass.autocomplete = mode === "register" ? "new-password" : "current-password";
  if (document.body.classList.contains("admin-ready")) {
    els.workspace.classList.remove("is-visible");
    els.workspace.classList.add("is-leaving");
    await wait(280);
    els.workspace.hidden = true;
    els.workspace.classList.add("field-hidden");
    els.workspace.classList.remove("is-leaving");
    document.body.classList.remove("admin-ready");
    els.authCard.classList.add("is-leaving");
    els.authCard.hidden = false;
    els.authCard.classList.remove("field-hidden");
    await wait(20);
    els.authCard.classList.remove("is-leaving");
    return;
  }
  els.authCard.hidden = false;
  els.authCard.classList.remove("field-hidden", "is-leaving");
  els.workspace.hidden = true;
  els.workspace.classList.add("field-hidden");
  els.workspace.classList.remove("is-visible");
}

async function showWorkspace(username, { fromAuth = true } = {}) {
  setCurrentUsername(username);
  showPage("model");
  if (fromAuth && !els.authCard.hidden) {
    els.authCard.classList.add("is-leaving");
    await wait(280);
  }
  els.authCard.hidden = true;
  els.authCard.classList.add("field-hidden");
  els.authCard.classList.remove("is-leaving");
  document.body.classList.add("admin-ready");
  els.workspace.hidden = false;
  els.workspace.classList.remove("field-hidden", "is-leaving");
  els.workspace.classList.remove("is-visible");
  await wait(20);
  els.workspace.classList.add("is-visible");
}

function showPage(page) {
  document.querySelectorAll(".ga-page").forEach((el) => {
    const on = el.id === `page-${page}`;
    el.hidden = !on;
    el.classList.toggle("field-hidden", !on);
  });
  document.querySelectorAll(".ga-nav-item[data-page]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });
  if (page === "chats") loadChatRecords();
}

async function loadModel() {
  const res = await fetch("/api/admin/model");
  const data = await res.json();
  if (!res.ok) throw new Error(errorText(data));
  keysByProvider = data.keys_by_provider || {};
  const providerId = PROVIDERS.some((p) => p.id === data.provider) ? data.provider : "custom";
  if (!selectsReady) {
    fillProviderSelect();
    selectsReady = true;
    lastProvider = els.provider.value;
    els.provider.addEventListener("change", () => {
      keysByProvider[lastProvider] = els.openaiKey.value;
      lastProvider = els.provider.value;
      applyProvider(currentProvider());
    });
  }
  els.provider.value = providerId;
  lastProvider = providerId;
  if (data.openai_key && !keysByProvider[providerId]) keysByProvider[providerId] = data.openai_key;
  els.openaiBase.value = data.openai_base || "";
  applyProvider(currentProvider(), {
    keepBase: Boolean(data.openai_base),
    savedModel: data.openai_model || "",
  });
  if (data.openai_model && currentProvider().id === "custom") {
    els.openaiModel.value = data.openai_model;
  }
  els.openaiKey.value = data.openai_key || keysByProvider[providerId] || "";
  if (els.systemPrompt) els.systemPrompt.value = data.system_prompt || "";
}

async function savePrompt() {
  const res = await fetch("/api/admin/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      system_prompt: els.systemPrompt.value,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data));
  if (els.systemPrompt) els.systemPrompt.value = data.system_prompt || els.systemPrompt.value;
  els.promptStatus.textContent = "已保存";
}

async function saveModel() {
  keysByProvider[els.provider.value] = els.openaiKey.value;
  const res = await fetch("/api/admin/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: els.provider.value,
      openai_base: els.openaiBase.value.trim(),
      openai_key: els.openaiKey.value,
      openai_model: selectedModel(),
      keys_by_provider: keysByProvider,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data));
  els.modelStatus.textContent = "已保存";
}

async function loadMcp() {
  const res = await fetch("/api/admin/mcp");
  const data = await res.json();
  if (!res.ok) throw new Error(errorText(data));
  els.mcpUrl.value = data.mcp_url || "";
  setMcpHeat(data.mcp_heat);
  applyOauthStatus(data);
}

async function saveMcp() {
  const heat = parseMcpHeat(els.mcpHeat?.value, 50);
  const res = await fetch("/api/admin/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mcp_url: els.mcpUrl.value.trim(),
      mcp_heat: heat,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data));
  await loadMcp();
  els.mcpStatus.textContent = "已保存";
}

async function loadQq() {
  const res = await fetch("/api/admin/qq");
  const data = await res.json();
  if (!res.ok) throw new Error(errorText(data));
  applyQqForm(data);
}

function applyOauthStatus(data) {
  const connected = Boolean(data?.oauth_connected);
  const text = connected ? "知识库已登录" : "未登录";
  document.querySelectorAll("[data-oauth-status]").forEach((el) => {
    el.textContent = text;
  });
  [els.btnMcpOauthLogout, els.btnQqOauthLogout].forEach((btn) => {
    if (btn) btn.hidden = !connected;
  });
}

function oauthButtons() {
  return [els.btnMcpOauth, els.btnQqOauth].filter(Boolean);
}

const OAUTH_LABEL = "网协认证登陆";
let oauthPending = false;
let oauthResetTimer = 0;

function setOauthLabel(text) {
  oauthButtons().forEach((btn) => {
    const span = btn.querySelector(".text");
    if (!span || span.textContent === text) return;
    span.style.opacity = "0";
    setTimeout(() => {
      span.textContent = text;
      span.style.opacity = "1";
    }, 250);
  });
}

function setOauthActive(on) {
  oauthPending = on;
  oauthButtons().forEach((btn) => {
    if (!on && btn.classList.contains("is-error")) {
      btn.classList.add("is-leaving-error");
      btn.classList.remove("is-error", "is-active");
      setTimeout(() => btn.classList.remove("is-leaving-error"), 800);
    } else {
      btn.classList.toggle("is-active", on);
      btn.classList.remove("is-error", "is-leaving-error");
    }
    btn.setAttribute("aria-busy", on ? "true" : "false");
  });
  setOauthLabel(on ? "登陆中" : OAUTH_LABEL);
}

function failOauthButton(message) {
  oauthPending = true;
  oauthButtons().forEach((btn) => {
    btn.classList.remove("is-leaving-error");
    btn.classList.add("is-active", "is-error");
    btn.setAttribute("aria-busy", "false");
  });
  setOauthLabel("登陆失败");
  document.querySelectorAll("[data-oauth-status]").forEach((el) => {
    el.textContent = message || "登录未完成";
  });
  clearTimeout(oauthResetTimer);
  oauthResetTimer = setTimeout(() => setOauthActive(false), 2500);
}

async function persistAdminOauth(extra = {}) {
  const res = await fetch("/api/admin/oauth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(extra),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data));
  applyOauthStatus(data);
  return data;
}

async function finishAdminOauth(extra = {}) {
  await persistAdminOauth(extra);
  setOauthActive(false);
  document.querySelectorAll("[data-oauth-status]").forEach((el) => {
    el.textContent = "知识库已登录";
  });
}

async function startAdminOauth() {
  if (oauthPending) return;
  clearTimeout(oauthResetTimer);
  setOauthActive(true);
  const url = (els.mcpUrl?.value || "").trim();
  if (url) {
    try {
      const res = await fetch("/api/admin/mcp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mcp_url: url }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorText(data));
    } catch (err) {
      failOauthButton(err.message || "请先保存网协 MCP 地址");
      return;
    }
  }
  const popup = window.open(
    "/api/mcp/oauth/start",
    "outline-oauth",
    "width=520,height=740,menubar=no,toolbar=no,status=no"
  );
  if (!popup) {
    failOauthButton("浏览器拦截了登录弹窗，请允许本站弹出窗口后重试");
    return;
  }
  document.querySelectorAll("[data-oauth-status]").forEach((el) => {
    el.textContent = "请在弹出窗口中用企业账号密码登录…";
  });
  popup.focus();
  const timer = setInterval(() => {
    if (!popup.closed) return;
    clearInterval(timer);
    persistAdminOauth()
      .then(() => setOauthActive(false))
      .catch(() => {
        if (!oauthPending) return;
        failOauthButton("登录未完成，请再试一次");
      });
  }, 600);
}

async function logoutAdminOauth() {
  const res = await fetch("/api/admin/oauth/logout", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data));
  applyOauthStatus({ oauth_connected: false });
}

function applyQqForm(data) {
  if (els.qqAppId) els.qqAppId.value = data.app_id || "";
  if (els.qqAppSecret) els.qqAppSecret.value = data.app_secret || "";
  if (els.qqEnabled) els.qqEnabled.checked = Boolean(data.enabled);
  applyOauthStatus(data);
  if (!els.qqStatus) return;
  if (data.error) els.qqStatus.textContent = data.error;
  else if (data.running) els.qqStatus.textContent = "机器人运行中";
  else if (data.enabled) els.qqStatus.textContent = "已开启，正在连接…";
  else els.qqStatus.textContent = "";
}

function qqPayload() {
  return {
    app_id: (els.qqAppId?.value || "").trim(),
    app_secret: els.qqAppSecret?.value || "",
    enabled: Boolean(els.qqEnabled?.checked),
  };
}

async function saveQq() {
  const res = await fetch("/api/admin/qq", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(qqPayload()),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data));
  applyQqForm(data);
  if (!data.error) els.qqStatus.textContent = data.running ? "已保存，机器人运行中" : "已保存";
}

function mcpHeatHint(value) {
  if (value <= 0) return "不调用知识库，直接作答";
  if (value < 35) return "少检索，尽快结束";
  if (value < 75) return "检索到相关文档后即可作答";
  return "充分检索，必要时阅读多篇文档";
}

function parseMcpHeat(value, fallback = 50) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, minHeat(n));
}

function minHeat(n) {
  return Math.min(100, Math.round(n));
}

function setMcpHeat(value) {
  const n = parseMcpHeat(value, 50);
  if (els.mcpHeat) els.mcpHeat.value = String(n);
  if (els.mcpHeatValue) els.mcpHeatValue.textContent = String(n);
  if (els.mcpHeatHint) els.mcpHeatHint.textContent = mcpHeatHint(n);
}

async function loadWorkspace(username, { fromAuth = true } = {}) {
  const fade = showWorkspace(username, { fromAuth });
  await Promise.all([loadModel(), loadMcp(), loadQq(), loadAdmins(), fade]);
}

function attachRipple() {
  document.addEventListener("pointerdown", (event) => {
    const btn = event.target.closest(".btn, .ga-nav-item");
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

async function boot() {
  attachRipple();
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".md-select")) {
      document.querySelectorAll(".md-select.open").forEach((el) => el.classList.remove("open"));
    }
  });
  const res = await fetch("/api/admin/status");
  const status = await res.json();
  if (!status.gate) {
    els.authStatus.textContent = "请从启动控制台打印的管理员网址进入。";
    return;
  }
  if (!status.setup) {
    showAuth("register");
  } else if (!status.logged_in) {
    showAuth("login");
  } else {
    await loadWorkspace(status.username, { fromAuth: false });
  }

  document.querySelectorAll(".ga-nav-item[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => showPage(btn.dataset.page));
  });

  els.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    els.authStatus.textContent = "";
    const path = authMode === "register" ? "/api/admin/register" : "/api/admin/login";
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: els.adminUser.value,
        password: els.adminPass.value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      els.authStatus.textContent = errorText(data);
      return;
    }
    try {
      await loadWorkspace(els.adminUser.value.trim());
    } catch (err) {
      els.modelStatus.textContent = err.message;
    }
  });

  els.btnSaveModel.addEventListener("click", () => {
    els.modelStatus.textContent = "正在保存…";
    saveModel().catch((err) => {
      els.modelStatus.textContent = err.message;
    });
  });

  els.btnSavePrompt.addEventListener("click", () => {
    els.promptStatus.textContent = "正在保存…";
    savePrompt().catch((err) => {
      els.promptStatus.textContent = err.message;
    });
  });

  els.btnSaveMcp.addEventListener("click", () => {
    els.mcpStatus.textContent = "正在保存…";
    saveMcp().catch((err) => {
      els.mcpStatus.textContent = err.message;
    });
  });
  oauthButtons().forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      startAdminOauth();
    });
  });
  [els.btnMcpOauthLogout, els.btnQqOauthLogout].forEach((btn) => {
    if (!btn) return;
    btn.addEventListener("click", () => {
      logoutAdminOauth().catch((err) => {
        document.querySelectorAll("[data-oauth-status]").forEach((el) => {
          el.textContent = err.message;
        });
      });
    });
  });
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    const data = event.data || {};
    if (data.type !== "outline-oauth") return;
    if (data.status === "ok") {
      finishAdminOauth({
        access_token: data.access_token || "",
        refresh_token: data.refresh_token || "",
      }).catch((err) => failOauthButton(err.message || "登录凭证保存失败"));
      return;
    }
    const map = {
      missing_url: "请先填写并保存网协 MCP 地址",
      start: "无法打开企业登录：" + (data.message || ""),
      missing: "登录未完成",
      state: "登录校验失败，请再试一次",
      token: "登录换票失败：" + (data.message || ""),
    };
    failOauthButton(map[data.status] || "登录未完成");
  });
  if (els.btnSaveQq) {
    els.btnSaveQq.addEventListener("click", () => {
      els.qqStatus.textContent = "正在保存…";
      saveQq().catch((err) => {
        els.qqStatus.textContent = err.message;
      });
    });
  }
  if (els.qqEnabled) {
    els.qqEnabled.addEventListener("change", () => {
      els.qqStatus.textContent = els.qqEnabled.checked ? "正在开启…" : "正在关闭…";
      saveQq().catch((err) => {
        els.qqEnabled.checked = !els.qqEnabled.checked;
        els.qqStatus.textContent = err.message;
      });
    });
  }
  if (els.mcpHeat) {
    els.mcpHeat.addEventListener("input", () => setMcpHeat(els.mcpHeat.value));
  }

  els.btnLogout.addEventListener("click", async () => {
    await fetch("/api/admin/logout", { method: "POST" });
    await showAuth("login");
    els.adminPass.value = "";
  });

  els.btnAddAdmin.addEventListener("click", () => openAdminDialog("add"));
  els.btnDialogCancel.addEventListener("click", () => closeAdminDialog());
  els.adminDialogBackdrop.addEventListener("click", () => closeAdminDialog());
  els.btnDialogDelete.addEventListener("click", async () => {
    if (dialogMode !== "edit" || !editingUsername) return;
    els.adminDialogStatus.textContent = "";
    const res = await fetch("/api/admin/users", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: editingUsername }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      els.adminDialogStatus.textContent = errorText(data);
      return;
    }
    renderAdminList(data.usernames || []);
    await closeAdminDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && els.adminDialog.classList.contains("is-open")) {
      closeAdminDialog();
    }
  });
  els.adminDialogForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    els.adminDialogStatus.textContent = "";
    const isEdit = dialogMode === "edit";
    const res = await fetch("/api/admin/users", {
      method: isEdit ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        old_username: editingUsername,
        username: els.newAdminUser.value,
        password: els.newAdminPass.value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      els.adminDialogStatus.textContent = errorText(data);
      return;
    }
    if (data.username && isEdit && editingUsername === currentUsername) {
      setCurrentUsername(data.username);
    }
    renderAdminList(data.usernames || []);
    await closeAdminDialog();
  });
}

boot().catch((err) => {
  els.authStatus.textContent = err.message;
});
