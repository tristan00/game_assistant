// game_assistant web frontend.
// Talks REST + WebSocket to /api and /ws served by app/web_server.py.

const MODELS = [
  "claude-sonnet-4-6",
  "claude-opus-4-7",
  "claude-opus-4-6",
  "claude-haiku-4-5-20251001",
];

// ----- Element helpers ------------------------------------------------------

const $ = (id) => document.getElementById(id);

const els = {
  windowSelect: $("window-select"),
  refreshBtn: $("refresh-btn"),
  captureBtn: $("capture-btn"),
  sessionLabel: $("session-label"),
  intervalInput: $("interval-input"),
  newSessionBtn: $("new-session-btn"),
  modelSelect: $("model-select"),
  lastNInput: $("last-n-input"),
  webSearchInput: $("web-search-input"),
  settingsBtn: $("settings-btn"),
  apiKeyBtn: $("api-key-btn"),
  strategySelect: $("strategy-select"),
  newStrategyBtn: $("new-strategy-btn"),
  editStrategyBtn: $("edit-strategy-btn"),
  saveStrategyBtn: $("save-strategy-btn"),
  deleteStrategyBtn: $("delete-strategy-btn"),
  strategyEditor: $("strategy-editor"),
  question: $("question"),
  submitBtn: $("submit-btn"),
  pending: $("pending"),
  chat: $("chat"),
  status: $("status"),
  hotkeyHint: $("hotkey-hint"),
  settingsModal: $("settings-modal"),
  settingsModel: $("settings-model"),
  settingsInterval: $("settings-interval"),
  settingsLastN: $("settings-last-n"),
  settingsWebSearch: $("settings-web-search"),
  settingsHotkey: $("settings-hotkey"),
  settingsCancel: $("settings-cancel"),
  settingsSave: $("settings-save"),
  apiKeyModal: $("api-key-modal"),
  apiKeyInput: $("api-key-input"),
  apiKeyCancel: $("api-key-cancel"),
  apiKeySave: $("api-key-save"),
  apiKeyTitle: $("api-key-title"),
};

// ----- State ----------------------------------------------------------------

let state = {
  settings: null,
  selectedHwnd: null,
  history: [],
  strategies: [],
  activeStrategy: "",
  activeStrategyContent: "",
  strategyEditing: false,
  inFlight: false,
  inFlightStartedAt: null,
  inFlightExcerpt: "",
  inFlightModel: "",
  inFlightNImages: 0,
};

let pendingTickHandle = null;

// ----- REST helpers ---------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${method} ${path} -> ${res.status}: ${text}`);
  }
  return res.json();
}

// ----- Rendering ------------------------------------------------------------

function setStatus(text) { els.status.textContent = text; }

function fmtElapsed(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function appendTurn(question, response, elapsedStr) {
  const div = document.createElement("div");
  div.className = "turn";
  div.innerHTML = `
    <div class="you-label">▶ You</div>
    <div class="body">${escapeHtml(question)}</div>
    <div class="asst-label">◀ Assistant <span class="elapsed">(${elapsedStr})</span></div>
    <div class="body">${escapeHtml(response)}</div>
    <hr />
  `;
  els.chat.appendChild(div);
  els.chat.scrollTop = els.chat.scrollHeight;
}

function appendError(errorText, elapsedStr) {
  const div = document.createElement("div");
  div.className = "turn";
  div.innerHTML = `
    <div class="err-label">✕ Error <span class="elapsed">(after ${elapsedStr})</span></div>
    <div class="body error">${escapeHtml(errorText)}</div>
    <hr />
  `;
  els.chat.appendChild(div);
  els.chat.scrollTop = els.chat.scrollHeight;
}

function renderHistory() {
  els.chat.innerHTML = "";
  for (const turn of state.history) {
    // Re-rendering history doesn't preserve original elapsed times; show — instead.
    appendTurn(turn.question, turn.response, "—");
  }
}

function renderWindows(windows) {
  const prev = state.selectedHwnd;
  els.windowSelect.innerHTML = "";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "(pick a window)";
  els.windowSelect.appendChild(noneOpt);
  for (const w of windows) {
    const opt = document.createElement("option");
    opt.value = String(w.hwnd);
    const title = w.title.length > 80 ? w.title.slice(0, 77) + "…" : w.title;
    opt.textContent = `${title}  [hwnd ${w.hwnd}]`;
    els.windowSelect.appendChild(opt);
  }
  if (prev !== null && [...els.windowSelect.options].some(o => o.value === String(prev))) {
    els.windowSelect.value = String(prev);
  }
}

function renderStrategies() {
  els.strategySelect.innerHTML = "";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "(none)";
  els.strategySelect.appendChild(noneOpt);
  for (const name of state.strategies) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    els.strategySelect.appendChild(opt);
  }
  els.strategySelect.value = state.activeStrategy || "";
  els.strategyEditor.value = state.activeStrategyContent || "";
  els.strategyEditor.readOnly = true;
  els.editStrategyBtn.disabled = !state.activeStrategy;
  els.saveStrategyBtn.disabled = true;
  els.deleteStrategyBtn.disabled = !state.activeStrategy;
  state.strategyEditing = false;
}

function applySettings(s) {
  state.settings = s;
  els.intervalInput.value = s.interval_seconds;
  els.lastNInput.value = s.last_n;
  els.webSearchInput.value = s.web_search_max_uses;
  // Populate model select if empty.
  if (els.modelSelect.options.length === 0) {
    for (const m of MODELS) {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      els.modelSelect.appendChild(opt);
    }
  }
  els.modelSelect.value = s.model;
  els.hotkeyHint.textContent = s.hotkey_qt || "Ctrl+Alt+S";
}

function renderSession(folder, total) {
  els.sessionLabel.textContent = `Active session: ${folder} — ${total} shots`;
}

function startPendingTicker() {
  stopPendingTicker();
  function tick() {
    if (!state.inFlight || !state.inFlightStartedAt) return;
    const seconds = (Date.now() - state.inFlightStartedAt) / 1000;
    els.pending.textContent =
      `⏳ Waiting on ${state.inFlightModel} (${state.inFlightNImages} image${state.inFlightNImages === 1 ? '' : 's'}) — ${fmtElapsed(seconds)}\n`
      + `You: ${state.inFlightExcerpt}`;
  }
  tick();
  pendingTickHandle = setInterval(tick, 500);
  els.pending.classList.remove("hidden");
}

function stopPendingTicker() {
  if (pendingTickHandle !== null) {
    clearInterval(pendingTickHandle);
    pendingTickHandle = null;
  }
  els.pending.classList.add("hidden");
}

// ----- Event handlers (REST) ------------------------------------------------

async function refreshWindows() {
  try {
    const windows = await api("GET", "/api/windows");
    renderWindows(windows);
    setStatus(`${windows.length} windows.`);
  } catch (e) {
    setStatus(`Refresh failed: ${e.message}`);
  }
}

async function selectWindow() {
  const v = els.windowSelect.value;
  const hwnd = v === "" ? null : parseInt(v, 10);
  state.selectedHwnd = hwnd;
  await api("PUT", "/api/window", { hwnd });
}

async function captureNow() {
  try {
    await api("POST", "/api/capture");
  } catch (e) {
    setStatus(`Capture failed: ${e.message}`);
  }
}

async function newSession() {
  await api("POST", "/api/session/new");
  state.history = [];
  renderHistory();
}

async function saveSetting(key, value) {
  await api("PUT", "/api/settings", { [key]: value });
}

async function selectStrategy() {
  const name = els.strategySelect.value;
  if (state.strategyEditing) {
    if (!confirm("Discard unsaved changes to the current strategy?")) {
      els.strategySelect.value = state.activeStrategy || "";
      return;
    }
  }
  await api("PUT", "/api/active_strategy", { name });
  // Server returns updated content via settings_changed + we fetch via /api/state too.
  const fresh = await api("GET", "/api/state");
  state.activeStrategy = fresh.active_strategy;
  state.activeStrategyContent = fresh.active_strategy_content;
  renderStrategies();
}

async function newStrategy() {
  const name = prompt("Strategy name (letters, digits, dashes, underscores, spaces):");
  if (!name || !name.trim()) return;
  try {
    const res = await api("POST", "/api/strategies", { name: name.trim() });
    await api("PUT", "/api/active_strategy", { name: res.name });
    const fresh = await api("GET", "/api/state");
    Object.assign(state, {
      strategies: fresh.strategies,
      activeStrategy: fresh.active_strategy,
      activeStrategyContent: fresh.active_strategy_content,
    });
    renderStrategies();
    enterEditMode();
  } catch (e) {
    alert(`Create failed: ${e.message}`);
  }
}

function enterEditMode() {
  if (!state.activeStrategy) return;
  state.strategyEditing = true;
  els.strategyEditor.readOnly = false;
  els.editStrategyBtn.disabled = true;
  els.saveStrategyBtn.disabled = false;
  els.deleteStrategyBtn.disabled = true;
  els.newStrategyBtn.disabled = true;
  els.strategySelect.disabled = true;
  els.strategyEditor.focus();
}

async function saveStrategy() {
  if (!state.activeStrategy) return;
  const content = els.strategyEditor.value;
  try {
    await api("PUT", `/api/strategies/${encodeURIComponent(state.activeStrategy)}`, { content });
    state.activeStrategyContent = content;
    state.strategyEditing = false;
    els.strategyEditor.readOnly = true;
    els.editStrategyBtn.disabled = false;
    els.saveStrategyBtn.disabled = true;
    els.deleteStrategyBtn.disabled = false;
    els.newStrategyBtn.disabled = false;
    els.strategySelect.disabled = false;
    setStatus(`Saved strategy '${state.activeStrategy}' (${content.length} chars).`);
  } catch (e) {
    alert(`Save failed: ${e.message}`);
  }
}

async function deleteStrategy() {
  if (!state.activeStrategy) return;
  if (!confirm(`Delete strategy '${state.activeStrategy}'? The .md file will be removed from disk.`)) return;
  await api("DELETE", `/api/strategies/${encodeURIComponent(state.activeStrategy)}`);
  state.activeStrategy = "";
  state.activeStrategyContent = "";
  const fresh = await api("GET", "/api/state");
  state.strategies = fresh.strategies;
  renderStrategies();
}

async function submitQuestion() {
  const q = els.question.value.trim();
  if (!q) {
    setStatus("Type a question first.");
    return;
  }
  if (state.inFlight) return;
  if (!state.selectedHwnd) {
    setStatus("Pick a window first.");
    return;
  }
  try {
    await api("POST", "/api/submit", { question: q });
    els.question.value = "";
  } catch (e) {
    if (e.message.includes("no API key set")) {
      openApiKeyModal(true);
    } else {
      setStatus(`Submit failed: ${e.message}`);
    }
  }
}

// ----- Settings modal -------------------------------------------------------

function openSettingsModal() {
  if (els.settingsModel.options.length === 0) {
    for (const m of MODELS) {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      els.settingsModel.appendChild(opt);
    }
  }
  els.settingsModel.value = state.settings.model;
  els.settingsInterval.value = state.settings.interval_seconds;
  els.settingsLastN.value = state.settings.last_n;
  els.settingsWebSearch.value = state.settings.web_search_max_uses;
  els.settingsHotkey.value = state.settings.hotkey_qt;
  els.settingsModal.classList.remove("hidden");
}

async function saveSettingsModal() {
  const updates = {
    model: els.settingsModel.value,
    interval_seconds: parseInt(els.settingsInterval.value, 10),
    last_n: parseInt(els.settingsLastN.value, 10),
    web_search_max_uses: parseInt(els.settingsWebSearch.value, 10),
    hotkey_qt: els.settingsHotkey.value.trim() || "Ctrl+Alt+S",
  };
  await api("PUT", "/api/settings", updates);
  els.settingsModal.classList.add("hidden");
}

// ----- API key modal --------------------------------------------------------

function openApiKeyModal(firstRun = false) {
  els.apiKeyInput.value = "";
  els.apiKeyTitle.textContent = firstRun ? "Anthropic API key (first-run setup)" : "Anthropic API key";
  els.apiKeyModal.classList.remove("hidden");
  els.apiKeyInput.focus();
}

async function saveApiKeyModal() {
  const key = els.apiKeyInput.value.trim();
  if (!key) {
    els.apiKeyModal.classList.add("hidden");
    return;
  }
  try {
    await api("PUT", "/api/api_key", { key });
    els.apiKeyModal.classList.add("hidden");
    setStatus("API key saved.");
  } catch (e) {
    alert(`Save failed: ${e.message}`);
  }
}

// ----- WebSocket handling ---------------------------------------------------

let ws = null;
let wsBackoff = 1000;

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { wsBackoff = 1000; };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      handleWsEvent(msg);
    } catch (e) {
      console.error("ws message parse failed", e, ev.data);
    }
  };
  ws.onclose = () => {
    ws = null;
    setTimeout(connectWs, wsBackoff);
    wsBackoff = Math.min(wsBackoff * 2, 15000);
  };
  ws.onerror = (e) => { console.warn("ws error", e); };
}

function handleWsEvent(msg) {
  switch (msg.type) {
    case "snapshot": {
      const s = msg.state;
      applySettings(s.settings);
      renderSession(s.session.folder_name, s.session.total_shots);
      renderWindows(s.windows);
      state.selectedHwnd = s.selected_hwnd;
      if (state.selectedHwnd !== null) {
        els.windowSelect.value = String(state.selectedHwnd);
      }
      state.history = s.history;
      renderHistory();
      state.strategies = s.strategies;
      state.activeStrategy = s.active_strategy;
      state.activeStrategyContent = s.active_strategy_content;
      renderStrategies();
      if (s.in_flight) {
        state.inFlight = true;
        state.inFlightStartedAt = Date.parse(s.in_flight_started_iso);
        state.inFlightModel = s.in_flight_model;
        state.inFlightNImages = s.in_flight_n_images;
        state.inFlightExcerpt = (s.in_flight_question || "").slice(0, 80);
        startPendingTicker();
        els.submitBtn.disabled = true;
        els.question.disabled = true;
      }
      if (!s.has_api_key) openApiKeyModal(true);
      break;
    }
    case "capture_saved":
      renderSession(msg.session_folder, msg.total_shots);
      setStatus(`${msg.source}: saved ${msg.filename} (${msg.bytes.toLocaleString()} bytes)`);
      break;
    case "capture_error":
      setStatus(`Capture failed (${msg.source}). See server log.`);
      break;
    case "session_changed":
      renderSession(msg.folder_name, msg.total_shots);
      break;
    case "history_cleared":
      state.history = [];
      renderHistory();
      break;
    case "settings_changed":
      applySettings(msg.settings);
      break;
    case "strategy_list_changed":
      state.strategies = msg.strategies;
      renderStrategies();
      break;
    case "submit_started":
      state.inFlight = true;
      state.inFlightStartedAt = Date.parse(msg.started_iso);
      state.inFlightExcerpt = msg.question_excerpt;
      state.inFlightModel = msg.model;
      state.inFlightNImages = msg.n_images;
      startPendingTicker();
      els.submitBtn.disabled = true;
      els.question.disabled = true;
      setStatus(`Sending ${msg.n_images} image(s) to ${msg.model}…`);
      break;
    case "submit_result": {
      state.inFlight = false;
      stopPendingTicker();
      els.submitBtn.disabled = false;
      els.question.disabled = false;
      const elapsedStr = fmtElapsed(msg.elapsed_seconds);
      state.history = msg.history;
      appendTurn(msg.question, msg.response, elapsedStr);
      setStatus(`Response received in ${elapsedStr}.`);
      break;
    }
    case "submit_error": {
      state.inFlight = false;
      stopPendingTicker();
      els.submitBtn.disabled = false;
      els.question.disabled = false;
      const elapsedStr = fmtElapsed(msg.elapsed_seconds);
      appendError(msg.error, elapsedStr);
      setStatus(`Submit failed after ${elapsedStr} (see Chat).`);
      break;
    }
  }
}

// ----- Wire up event listeners ----------------------------------------------

function wire() {
  els.refreshBtn.addEventListener("click", refreshWindows);
  els.captureBtn.addEventListener("click", captureNow);
  els.windowSelect.addEventListener("change", selectWindow);
  els.newSessionBtn.addEventListener("click", newSession);

  els.intervalInput.addEventListener("change", () => saveSetting("interval_seconds", parseInt(els.intervalInput.value, 10)));
  els.modelSelect.addEventListener("change", () => saveSetting("model", els.modelSelect.value));
  els.lastNInput.addEventListener("change", () => saveSetting("last_n", parseInt(els.lastNInput.value, 10)));
  els.webSearchInput.addEventListener("change", () => saveSetting("web_search_max_uses", parseInt(els.webSearchInput.value, 10)));

  els.strategySelect.addEventListener("change", selectStrategy);
  els.newStrategyBtn.addEventListener("click", newStrategy);
  els.editStrategyBtn.addEventListener("click", enterEditMode);
  els.saveStrategyBtn.addEventListener("click", saveStrategy);
  els.deleteStrategyBtn.addEventListener("click", deleteStrategy);

  // Enter submits; Shift+Enter inserts newline.
  els.question.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      submitQuestion();
    }
  });
  els.submitBtn.addEventListener("click", submitQuestion);

  els.settingsBtn.addEventListener("click", openSettingsModal);
  els.settingsCancel.addEventListener("click", () => els.settingsModal.classList.add("hidden"));
  els.settingsSave.addEventListener("click", saveSettingsModal);

  els.apiKeyBtn.addEventListener("click", () => openApiKeyModal(false));
  els.apiKeyCancel.addEventListener("click", () => els.apiKeyModal.classList.add("hidden"));
  els.apiKeySave.addEventListener("click", saveApiKeyModal);
}

// ----- Init -----------------------------------------------------------------

wire();
connectWs();
