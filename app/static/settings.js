// game_assistant settings page.

const MODELS = [
  "claude-sonnet-4-6",
  "claude-opus-4-7",
  "claude-opus-4-6",
  "claude-haiku-4-5-20251001",
];

const $ = (id) => document.getElementById(id);

const els = {
  errBanner: $("error-banner"),
  gModel: $("g-model"),
  gInterval: $("g-interval"),
  gLastN: $("g-last-n"),
  gHotkey: $("g-hotkey"),
  gPromptCache: $("g-prompt-cache"),
  gStage1Model: $("g-stage1-model"),
  gStage2Model: $("g-stage2-model"),
  gGameIdModel: $("g-gameid-model"),
  gDiscoveryModel: $("g-discovery-model"),
  gQuickRefModel: $("g-quickref-model"),
  gSchemaBuilderModel: $("g-schemabuilder-model"),
  gWikiUa: $("g-wiki-ua"),
  gWikiRate: $("g-wiki-rate"),
  gToolIters: $("g-tool-iters"),
  gamesList: $("games-list"),
  schemaSelect: $("schema-game-select"),
  schemaEditor: $("schema-editor"),
  schemaSave: $("schema-save-btn"),
  schemaRegen: $("schema-regenerate-btn"),
  schemaStatus: $("schema-status"),
};

let state = {
  settings: null,
  games: [],          // wiki/games view
  currentSchemaGameId: null,
  currentSchemaContent: "",
  schemaDirty: false,
};

// ----- HTTP -----------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch (_) {}
    throw new Error(`${method} ${path} -> ${res.status}: ${detail}`);
  }
  return text ? JSON.parse(text) : {};
}

function showError(msg) {
  els.errBanner.textContent = msg;
  els.errBanner.classList.remove("hidden");
}

function clearError() {
  els.errBanner.textContent = "";
  els.errBanner.classList.add("hidden");
}

function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ----- General settings -----------------------------------------------------

function applySettings(s) {
  state.settings = s;
  if (els.gModel.options.length === 0) {
    for (const m of MODELS) {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      els.gModel.appendChild(opt);
    }
  }
  els.gModel.value = s.model;
  els.gInterval.value = s.interval_seconds;
  els.gLastN.value = s.last_n;
  els.gHotkey.value = s.hotkey_qt;
  els.gPromptCache.checked = !!s.enable_prompt_cache;
  els.gStage1Model.value = s.perception_stage1_model || "";
  els.gStage2Model.value = s.perception_stage2_model || "";
  els.gGameIdModel.value = s.game_id_model || "";
  els.gDiscoveryModel.value = s.wiki_discovery_model || "";
  els.gQuickRefModel.value = s.quick_ref_model || "";
  els.gSchemaBuilderModel.value = s.schema_builder_model || "";
  els.gWikiUa.value = s.wiki_user_agent || "";
  els.gWikiRate.value = s.wiki_rate_seconds ?? 1.0;
  els.gToolIters.value = s.client_tool_max_iters ?? 6;
}

async function loadSettings() {
  const s = await api("GET", "/api/settings");
  applySettings(s);
}

async function saveSettings(updates) {
  try {
    const fresh = await api("PUT", "/api/settings", updates);
    applySettings(fresh);
    clearError();
  } catch (e) {
    showError(`Save settings failed: ${e.message}`);
  }
}

function wireSettings() {
  els.gModel.addEventListener("change", () => saveSettings({ model: els.gModel.value }));
  els.gInterval.addEventListener("change", () => saveSettings({ interval_seconds: parseInt(els.gInterval.value, 10) }));
  els.gLastN.addEventListener("change", () => saveSettings({ last_n: parseInt(els.gLastN.value, 10) }));
  els.gHotkey.addEventListener("change", () => saveSettings({ hotkey_qt: els.gHotkey.value.trim() || "Ctrl+Alt+S" }));
  els.gPromptCache.addEventListener("change", () => saveSettings({ enable_prompt_cache: !!els.gPromptCache.checked }));
  els.gStage1Model.addEventListener("change", () => saveSettings({ perception_stage1_model: els.gStage1Model.value.trim() }));
  els.gStage2Model.addEventListener("change", () => saveSettings({ perception_stage2_model: els.gStage2Model.value.trim() }));
  els.gGameIdModel.addEventListener("change", () => saveSettings({ game_id_model: els.gGameIdModel.value.trim() }));
  els.gDiscoveryModel.addEventListener("change", () => saveSettings({ wiki_discovery_model: els.gDiscoveryModel.value.trim() }));
  els.gQuickRefModel.addEventListener("change", () => saveSettings({ quick_ref_model: els.gQuickRefModel.value.trim() }));
  els.gSchemaBuilderModel.addEventListener("change", () => saveSettings({ schema_builder_model: els.gSchemaBuilderModel.value.trim() }));
  els.gWikiUa.addEventListener("change", () => saveSettings({ wiki_user_agent: els.gWikiUa.value.trim() }));
  els.gWikiRate.addEventListener("change", () => saveSettings({ wiki_rate_seconds: parseFloat(els.gWikiRate.value) }));
  els.gToolIters.addEventListener("change", () => saveSettings({ client_tool_max_iters: parseInt(els.gToolIters.value, 10) }));
}

// ----- Wiki game cards ------------------------------------------------------

function badgeClass(crawlState) {
  if (crawlState === "done") return "ok";
  if (crawlState === "running") return "run";
  if (crawlState === "failed") return "warn";
  return "";
}

function renderGames() {
  els.gamesList.innerHTML = "";
  if (state.games.length === 0) {
    const empty = document.createElement("div");
    empty.className = "muted small";
    empty.textContent = "No games registered yet. Pick a window on the main page to identify a game.";
    els.gamesList.appendChild(empty);
    return;
  }
  for (const g of state.games) {
    const card = document.createElement("div");
    card.className = "game-card";
    const stateLabel = g.crawl_state || "none";
    card.innerHTML = `
      <div class="head">
        <span class="name">${escapeHtml(g.display_name)}</span>
        <span class="badge">${escapeHtml(g.game_id)}</span>
        <span class="badge ${badgeClass(g.crawl_state)}">crawl: ${escapeHtml(stateLabel)}</span>
        <span class="badge">${g.page_count} page${g.page_count === 1 ? "" : "s"}</span>
        ${g.has_quick_ref ? `<span class="badge ok">quick-ref</span>` : ""}
        ${g.has_perception_schema ? `<span class="badge ok">schema</span>` : ""}
      </div>
      <div class="grid">
        <label>Wiki URL:</label>
        <input type="text" data-field="wiki_url" value="${escapeHtml(g.wiki_url || "")}" placeholder="https://example.fandom.com/wiki/" />
        <label>API URL:</label>
        <input type="text" data-field="api_url" value="${escapeHtml(g.api_url || "")}" placeholder="https://example.fandom.com/api.php" />
        <label>Root page:</label>
        <input type="text" data-field="root_page" value="${escapeHtml(g.root_page || "")}" placeholder="Main_Page" />
        ${g.sitename ? `<label>Sitename:</label><span class="muted">${escapeHtml(g.sitename)}</span>` : ""}
        ${g.last_crawl_iso ? `<label>Last crawl:</label><span class="muted small">${escapeHtml(g.last_crawl_iso)}</span>` : ""}
      </div>
      <div class="actions">
        <button class="ghost" data-action="delete">Delete corpus</button>
        <button class="ghost" data-action="rediscover">Re-run discovery</button>
        <button data-action="save">Save + re-crawl</button>
      </div>
    `;
    const saveBtn = card.querySelector('[data-action="save"]');
    const deleteBtn = card.querySelector('[data-action="delete"]');
    const rediscoverBtn = card.querySelector('[data-action="rediscover"]');
    saveBtn.addEventListener("click", () => saveGame(g.game_id, card));
    deleteBtn.addEventListener("click", () => deleteCorpus(g.game_id, g.display_name));
    rediscoverBtn.addEventListener("click", () => rediscover(g.game_id, g.display_name));
    els.gamesList.appendChild(card);
  }
  // Refresh schema-editor select.
  rebuildSchemaSelect();
}

async function loadGames() {
  try {
    const res = await api("GET", "/api/wiki/games");
    state.games = res.games || [];
    renderGames();
    clearError();
  } catch (e) {
    showError(`Load games failed: ${e.message}`);
  }
}

async function saveGame(gameId, card) {
  const fields = {};
  for (const inp of card.querySelectorAll("input[data-field]")) {
    fields[inp.dataset.field] = inp.value.trim();
  }
  try {
    await api("PUT", `/api/wiki/games/${encodeURIComponent(gameId)}`, fields);
    clearError();
    await loadGames();
  } catch (e) {
    showError(`Save failed: ${e.message}`);
  }
}

async function deleteCorpus(gameId, displayName) {
  if (!confirm(`Delete the local wiki corpus for '${displayName}'? This wipes all pages, the index, quick-ref, and perception schema for this game.`)) return;
  try {
    await api("DELETE", `/api/wiki/games/${encodeURIComponent(gameId)}/corpus`);
    clearError();
    await loadGames();
  } catch (e) {
    showError(`Delete failed: ${e.message}`);
  }
}

async function rediscover(gameId, displayName) {
  if (!confirm(`Wipe the local wiki for '${displayName}' and re-run discovery + crawl from scratch?`)) return;
  try {
    await api("POST", `/api/wiki/games/${encodeURIComponent(gameId)}/rediscover`, {});
    clearError();
    await loadGames();
  } catch (e) {
    showError(`Re-run discovery failed: ${e.message}`);
  }
}

// ----- Perception schema editor --------------------------------------------

function rebuildSchemaSelect() {
  const prev = els.schemaSelect.value;
  els.schemaSelect.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "(pick a game)";
  els.schemaSelect.appendChild(none);
  for (const g of state.games) {
    if (!g.has_perception_schema && !g.has_quick_ref) continue;
    const opt = document.createElement("option");
    opt.value = g.game_id;
    opt.textContent = g.has_perception_schema
      ? `${g.display_name}`
      : `${g.display_name} (no schema yet — regenerate to build)`;
    els.schemaSelect.appendChild(opt);
  }
  if (prev && [...els.schemaSelect.options].some(o => o.value === prev)) {
    els.schemaSelect.value = prev;
  }
}

async function loadSchema() {
  const gameId = els.schemaSelect.value;
  state.currentSchemaGameId = gameId || null;
  state.schemaDirty = false;
  els.schemaSave.disabled = true;
  if (!gameId) {
    els.schemaEditor.value = "";
    els.schemaStatus.textContent = "";
    return;
  }
  els.schemaStatus.textContent = "Loading…";
  try {
    const res = await api("GET", `/api/perception/schema/${encodeURIComponent(gameId)}`);
    state.currentSchemaContent = res.content || "";
    els.schemaEditor.value = state.currentSchemaContent;
    els.schemaStatus.textContent = `${state.currentSchemaContent.length} chars on disk`;
    clearError();
  } catch (e) {
    els.schemaEditor.value = "";
    els.schemaStatus.textContent = "";
    if (e.message.includes("404")) {
      showError(`No _perception_schema.md exists yet for ${gameId}. Click Regenerate to build one from the quick-ref.`);
    } else {
      showError(`Load schema failed: ${e.message}`);
    }
  }
}

function onSchemaEdit() {
  state.schemaDirty = els.schemaEditor.value !== state.currentSchemaContent;
  els.schemaSave.disabled = !state.schemaDirty || !state.currentSchemaGameId;
}

async function saveSchema() {
  if (!state.currentSchemaGameId) return;
  const content = els.schemaEditor.value;
  try {
    const res = await api(
      "PUT",
      `/api/perception/schema/${encodeURIComponent(state.currentSchemaGameId)}`,
      { content },
    );
    state.currentSchemaContent = res.content;
    state.schemaDirty = false;
    els.schemaSave.disabled = true;
    els.schemaStatus.textContent = `Saved (${content.length} chars).`;
    clearError();
    await loadGames();
  } catch (e) {
    showError(`Save schema failed: ${e.message}`);
  }
}

async function regenerateSchema() {
  const gameId = els.schemaSelect.value || state.currentSchemaGameId;
  if (!gameId) {
    showError("Pick a game first.");
    return;
  }
  if (!confirm("Rebuild the perception schema from the quick-ref for this game? Existing edits to _perception_schema.md will be overwritten.")) return;
  try {
    await api("POST", `/api/perception/schema/${encodeURIComponent(gameId)}/regenerate`, {});
    els.schemaStatus.textContent = "Regenerating in background…";
    clearError();
  } catch (e) {
    showError(`Regenerate failed: ${e.message}`);
  }
}

function wireSchema() {
  els.schemaSelect.addEventListener("change", loadSchema);
  els.schemaEditor.addEventListener("input", onSchemaEdit);
  els.schemaSave.addEventListener("click", saveSchema);
  els.schemaRegen.addEventListener("click", regenerateSchema);
}

// ----- WebSocket: refresh games/schema when crawler reports progress -------

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = async (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (["crawl_done", "crawl_error", "wiki_discovered", "wiki_not_found", "corpus_ready", "perception_schema_rebuilt"].includes(msg.type)) {
        await loadGames();
        if (msg.type === "perception_schema_rebuilt" && state.currentSchemaGameId === msg.game_id) {
          await loadSchema();
        }
      } else if (msg.type === "settings_changed") {
        applySettings(msg.settings);
      }
    } catch (e) {
      console.warn("settings ws parse:", e);
    }
  };
  ws.onclose = () => setTimeout(connectWs, 2000);
}

// ----- Init -----------------------------------------------------------------

(async () => {
  wireSettings();
  wireSchema();
  await loadSettings();
  await loadGames();
  connectWs();
})();
