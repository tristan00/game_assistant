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
  gameStatus: $("game-status"),
  reidentifyBtn: $("reidentify-btn"),
  sessionLabel: $("session-label"),
  intervalInput: $("interval-input"),
  newSessionBtn: $("new-session-btn"),
  modelSelect: $("model-select"),
  lastNInput: $("last-n-input"),
  apiKeyBtn: $("api-key-btn"),
  goalSelect: $("goal-select"),
  newGoalBtn: $("new-goal-btn"),
  editGoalBtn: $("edit-goal-btn"),
  saveGoalBtn: $("save-goal-btn"),
  deleteGoalBtn: $("delete-goal-btn"),
  goalEditor: $("goal-editor"),
  question: $("question"),
  submitBtn: $("submit-btn"),
  pending: $("pending"),
  chat: $("chat"),
  status: $("status"),
  hotkeyHint: $("hotkey-hint"),
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
  goals: [],
  activeGoal: "",
  activeGoalContent: "",
  goalEditing: false,
  inFlight: false,
  inFlightStartedAt: null,
  inFlightExcerpt: "",
  inFlightModel: "",
  inFlightNImages: 0,
  // Game identity
  activeGameId: null,
  activeGameName: null,
  activeGameIsNotAGame: false,
  activeGameCrawlState: "none",
  activeGamePageCount: 0,
  activeGameWikiUrl: null,
  identifyingStage: null,
  wikiStage: null,
  crawlProgress: null,
  // Ingredient state — what context the next submit will/won't have.
  hasApiKey: false,
  hasQuickRef: false,
  hasPerceptionSchema: false,
  quickRefBuilding: false,
  schemaBuilding: false,
  windowUnreachable: false,
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

function renderGoals() {
  els.goalSelect.innerHTML = "";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "(none)";
  els.goalSelect.appendChild(noneOpt);
  for (const name of state.goals) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    els.goalSelect.appendChild(opt);
  }
  els.goalSelect.value = state.activeGoal || "";
  els.goalEditor.value = state.activeGoalContent || "";
  els.goalEditor.readOnly = true;
  if (state.activeGoal) {
    els.goalEditor.classList.remove("hidden");
  } else {
    els.goalEditor.classList.add("hidden");
  }
  els.editGoalBtn.disabled = !state.activeGoal;
  els.saveGoalBtn.disabled = true;
  els.deleteGoalBtn.disabled = !state.activeGoal;
  state.goalEditing = false;
}

function applySettings(s) {
  state.settings = s;
  els.intervalInput.value = s.interval_seconds;
  els.lastNInput.value = s.last_n;
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

function renderGameStatus() {
  const parts = [];
  if (!state.hasApiKey) parts.push("⚠ No API key (click 'API key…')");
  if (state.windowUnreachable) parts.push("⚠ window unreachable");

  if (state.selectedHwnd === null) {
    parts.unshift("Game: — (pick a window)");
    els.gameStatus.textContent = parts.join(" · ");
    els.reidentifyBtn.disabled = true;
    return;
  }
  if (state.activeGameId === null) {
    let msg = "Game: identifying…";
    if (state.identifyingStage === "deferred_no_api_key") msg = "Game: identification waiting for API key";
    else if (state.identifyingStage === "no_screenshot") msg = "Game: identification needs a screenshot";
    else if (state.identifyingStage === "llm_failed") msg = "Game: identification failed (see logs)";
    else if (state.identifyingStage === "low_confidence") msg = "Game: identification inconclusive (low confidence)";
    parts.unshift(msg);
    els.gameStatus.textContent = parts.join(" · ");
    els.reidentifyBtn.disabled = false;
    return;
  }
  if (state.activeGameIsNotAGame) {
    parts.unshift("Not a game (no wiki context)");
    els.gameStatus.textContent = parts.join(" · ");
    els.reidentifyBtn.disabled = false;
    return;
  }
  const gameParts = [`Game: ${state.activeGameName || state.activeGameId}`];
  if (state.wikiStage === "not_found") {
    gameParts.push("⚠ no wiki — add URL in Settings");
  } else if (state.activeGameCrawlState === "running" && state.crawlProgress) {
    gameParts.push(`crawling (${state.crawlProgress.pages_written} pages)`);
  } else if (state.activeGameCrawlState === "running") {
    gameParts.push("crawling…");
  } else if (state.wikiStage === "discovering") {
    gameParts.push("discovering wiki…");
  } else if (state.activeGamePageCount > 0) {
    gameParts.push(`${state.activeGamePageCount} pages`);
  }
  // Ingredient state for the next submit.
  if (state.quickRefBuilding) gameParts.push("building quick-ref…");
  else if (!state.hasQuickRef && state.activeGamePageCount > 0) gameParts.push("⚠ no quick-ref");
  if (state.schemaBuilding) gameParts.push("building schema…");
  else if (!state.hasPerceptionSchema && state.hasQuickRef) gameParts.push("⚠ no schema");
  parts.unshift(gameParts.join(" — "));
  els.gameStatus.textContent = parts.join(" · ");
  els.reidentifyBtn.disabled = false;
}

function applyActiveGame(ev) {
  state.activeGameId = ev.game_id;
  state.activeGameName = ev.display_name;
  state.activeGameIsNotAGame = !!ev.is_not_a_game;
  state.activeGameCrawlState = ev.crawl_state || "none";
  state.activeGamePageCount = ev.page_count || 0;
  state.activeGameWikiUrl = ev.wiki_url || null;
  state.identifyingStage = null;
  state.crawlProgress = null;
  // Game changed — reset per-game ingredient state until the snapshot or
  // subsequent events tell us what's actually on disk.
  state.hasQuickRef = false;
  state.hasPerceptionSchema = false;
  state.quickRefBuilding = false;
  state.schemaBuilding = false;
  if (state.activeGameWikiUrl) {
    state.wikiStage = null;
  }
  renderGameStatus();
}

async function reidentifyGame() {
  if (state.selectedHwnd === null) return;
  state.activeGameId = null;
  state.identifyingStage = "started";
  state.crawlProgress = null;
  renderGameStatus();
  try {
    await api("POST", "/api/games/reidentify", {});
  } catch (e) {
    setStatus(`Re-identify failed: ${e.message}`);
  }
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
  state.activeGameId = null;
  state.activeGameName = null;
  state.activeGameIsNotAGame = false;
  state.activeGameCrawlState = "none";
  state.activeGamePageCount = 0;
  state.activeGameWikiUrl = null;
  state.identifyingStage = hwnd === null ? null : "started";
  state.crawlProgress = null;
  renderGameStatus();
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

async function selectGoal() {
  const name = els.goalSelect.value;
  if (state.goalEditing) {
    if (!confirm("Discard unsaved changes to the current goal?")) {
      els.goalSelect.value = state.activeGoal || "";
      return;
    }
  }
  await api("PUT", "/api/active_goal", { name });
  const fresh = await api("GET", "/api/state");
  state.activeGoal = fresh.active_goal;
  state.activeGoalContent = fresh.active_goal_content;
  renderGoals();
}

async function newGoal() {
  const name = prompt("Goal name (letters, digits, dashes, underscores, spaces):");
  if (!name || !name.trim()) return;
  try {
    const res = await api("POST", "/api/goals", { name: name.trim() });
    await api("PUT", "/api/active_goal", { name: res.name });
    const fresh = await api("GET", "/api/state");
    Object.assign(state, {
      goals: fresh.goals,
      activeGoal: fresh.active_goal,
      activeGoalContent: fresh.active_goal_content,
    });
    renderGoals();
    enterEditMode();
  } catch (e) {
    alert(`Create failed: ${e.message}`);
  }
}

function enterEditMode() {
  if (!state.activeGoal) return;
  state.goalEditing = true;
  els.goalEditor.classList.remove("hidden");
  els.goalEditor.readOnly = false;
  els.editGoalBtn.disabled = true;
  els.saveGoalBtn.disabled = false;
  els.deleteGoalBtn.disabled = true;
  els.newGoalBtn.disabled = true;
  els.goalSelect.disabled = true;
  els.goalEditor.focus();
}

async function saveGoal() {
  if (!state.activeGoal) return;
  const content = els.goalEditor.value;
  try {
    await api("PUT", `/api/goals/${encodeURIComponent(state.activeGoal)}`, { content });
    state.activeGoalContent = content;
    state.goalEditing = false;
    els.goalEditor.readOnly = true;
    els.editGoalBtn.disabled = false;
    els.saveGoalBtn.disabled = true;
    els.deleteGoalBtn.disabled = false;
    els.newGoalBtn.disabled = false;
    els.goalSelect.disabled = false;
    setStatus(`Saved goal '${state.activeGoal}' (${content.length} chars).`);
  } catch (e) {
    alert(`Save failed: ${e.message}`);
  }
}

async function deleteGoal() {
  if (!state.activeGoal) return;
  if (!confirm(`Delete goal '${state.activeGoal}'? The .md file will be removed from disk.`)) return;
  await api("DELETE", `/api/goals/${encodeURIComponent(state.activeGoal)}`);
  state.activeGoal = "";
  state.activeGoalContent = "";
  const fresh = await api("GET", "/api/state");
  state.goals = fresh.goals;
  renderGoals();
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
    state.hasApiKey = true;
    renderGameStatus();
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
      state.goals = s.goals;
      state.activeGoal = s.active_goal;
      state.activeGoalContent = s.active_goal_content;
      renderGoals();
      state.activeGameId = s.active_game_id;
      state.activeGameIsNotAGame = !!s.active_game_is_not_a_game;
      state.hasApiKey = !!s.has_api_key;
      if (s.active_game) {
        state.activeGameName = s.active_game.display_name;
        state.activeGameCrawlState = s.active_game.crawl_state || "none";
        state.activeGamePageCount = s.active_game.pages_on_disk ?? s.active_game.page_count ?? 0;
        state.activeGameWikiUrl = s.active_game.wiki_url || null;
        state.hasQuickRef = !!s.active_game.has_quick_ref;
        state.hasPerceptionSchema = !!s.active_game.has_perception_schema;
      } else {
        state.activeGameName = null;
        state.activeGameCrawlState = "none";
        state.activeGamePageCount = 0;
        state.activeGameWikiUrl = null;
        state.hasQuickRef = false;
        state.hasPerceptionSchema = false;
      }
      state.quickRefBuilding = false;
      state.schemaBuilding = false;
      state.windowUnreachable = false;
      renderGameStatus();
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
      if (state.windowUnreachable) {
        state.windowUnreachable = false;
        renderGameStatus();
      }
      break;
    case "capture_error":
      // Silent retry: surface as a passive status indicator that auto-clears
      // on the next successful capture. Don't kill the timer or push a modal.
      state.windowUnreachable = true;
      renderGameStatus();
      setStatus(`Capture failed (${msg.source}) — will retry on next tick.`);
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
    case "goal_list_changed":
      state.goals = msg.goals;
      renderGoals();
      break;
    case "active_game_changed":
      applyActiveGame(msg);
      break;
    case "game_identifying":
      state.identifyingStage = msg.stage;
      renderGameStatus();
      break;
    case "wiki_discovering":
      state.wikiStage = "discovering";
      renderGameStatus();
      break;
    case "wiki_discovered":
      state.wikiStage = null;
      setStatus(`Wiki discovered for ${state.activeGameName}: ${msg.wiki_url}`);
      break;
    case "wiki_not_found":
      state.wikiStage = "not_found";
      state.activeGameCrawlState = "none";
      renderGameStatus();
      setStatus(`No wiki found for ${msg.display_name}. Fix in Settings → Game knowledge sources.`);
      break;
    case "crawl_started":
      state.activeGameCrawlState = "running";
      state.crawlProgress = { pages_written: 0, frontier_size: 0, current_title: msg.root_title || "" };
      renderGameStatus();
      break;
    case "crawl_progress":
      state.activeGameCrawlState = "running";
      state.crawlProgress = {
        pages_written: msg.pages_written,
        frontier_size: msg.frontier_size,
        current_title: msg.current_title,
      };
      renderGameStatus();
      break;
    case "crawl_done":
      state.activeGameCrawlState = "done";
      state.activeGamePageCount = msg.pages_written;
      state.crawlProgress = null;
      renderGameStatus();
      setStatus(`Wiki crawl done: ${msg.pages_written} pages in ${msg.elapsed_seconds ? msg.elapsed_seconds.toFixed(1) : '?'}s.`);
      break;
    case "crawl_error":
      state.activeGameCrawlState = "failed";
      state.crawlProgress = null;
      renderGameStatus();
      setStatus(`Wiki crawl failed: ${msg.error || 'see logs'}.`);
      break;
    case "corpus_ready":
      setStatus(`Corpus ready for ${state.activeGameName || msg.game_id}: ${msg.page_count} pages indexed.`);
      break;
    case "quick_ref_building":
      if (msg.game_id === state.activeGameId) {
        state.quickRefBuilding = true;
        renderGameStatus();
      }
      break;
    case "quick_ref_done":
      if (msg.game_id === state.activeGameId) {
        state.quickRefBuilding = false;
        state.hasQuickRef = true;
        renderGameStatus();
      }
      break;
    case "schema_building":
      if (msg.game_id === state.activeGameId) {
        state.schemaBuilding = true;
        renderGameStatus();
      }
      break;
    case "schema_done":
      if (msg.game_id === state.activeGameId) {
        state.schemaBuilding = false;
        state.hasPerceptionSchema = true;
        renderGameStatus();
      }
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
  els.reidentifyBtn.addEventListener("click", reidentifyGame);
  els.newSessionBtn.addEventListener("click", newSession);

  els.intervalInput.addEventListener("change", () => saveSetting("interval_seconds", parseInt(els.intervalInput.value, 10)));
  els.modelSelect.addEventListener("change", () => saveSetting("model", els.modelSelect.value));
  els.lastNInput.addEventListener("change", () => saveSetting("last_n", parseInt(els.lastNInput.value, 10)));

  els.goalSelect.addEventListener("change", selectGoal);
  els.newGoalBtn.addEventListener("click", newGoal);
  els.editGoalBtn.addEventListener("click", enterEditMode);
  els.saveGoalBtn.addEventListener("click", saveGoal);
  els.deleteGoalBtn.addEventListener("click", deleteGoal);

  els.question.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      submitQuestion();
    }
  });
  els.submitBtn.addEventListener("click", submitQuestion);

  els.apiKeyBtn.addEventListener("click", () => openApiKeyModal(false));
  els.apiKeyCancel.addEventListener("click", () => els.apiKeyModal.classList.add("hidden"));
  els.apiKeySave.addEventListener("click", saveApiKeyModal);
}

// ----- Init -----------------------------------------------------------------

wire();
connectWs();
