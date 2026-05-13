# game_assistant

Windows desktop app that periodically screenshots a game window and lets you ask an AI assistant questions about what's happening on screen. When the app identifies the game, it works in the background to build a local wiki corpus and a per-game perception schema; submissions use whichever of those ingredients are ready at the moment, never blocking on background work. Conversation continues across turns so you can correct the assistant's read of the situation and get a revised answer. Currently uses the Anthropic API; designed to be provider-agnostic in future revisions.

---

## 1. Get an Anthropic API key

1. Sign in (or sign up) at <https://console.anthropic.com>.
2. Go to **Settings → API keys** (<https://console.anthropic.com/settings/keys>).
3. Click **Create Key**, copy the value (starts with `sk-ant-…`). Anthropic only shows it once — paste it somewhere safe.
4. The first launch of the app will prompt you for this key and store it in **Windows Credential Manager**. It never gets written to disk inside the project, and it does not get bundled into the `.exe`.

Vision API requests are billed against your Anthropic account. Pricing: <https://www.anthropic.com/pricing>.

---

## 2. Download and run

**Requirements**: Windows 10 or 11. Windows 11 ships the Edge WebView2 runtime in-box, which is what renders the UI. On Windows 10 most up-to-date installs have it via Edge; if you hit a "WebView2 not found" error, install <https://developer.microsoft.com/en-us/microsoft-edge/webview2/>.

1. Go to the [Releases page](https://github.com/tristan00/game_assistant/releases/latest).
2. Download `game_assistant.exe`.
3. Double-click to run. No Python, no clone, no build step.

Logs land at `%USERPROFILE%\game_assistant\logs\run.log` (useful if something misbehaves on a console-less exe).

---

## 3. Using the app

### First launch
A dialog asks for your Anthropic API key. Paste it. If you dismiss the dialog, a "⚠ No API key" warning stays in the status row at the top of the window — captures still happen, but every Submit will fail with the same API error you'd get from a broken key or no network. Update or rotate the key any time via the **API key…** button in the top bar.

### Pick a game window
The dropdown lists open windows. Pick the game (or any application — works fine for browsers, video players, etc., as long as the window is not occluded or fullscreen-exclusive). The capture timer starts automatically the moment a window is selected.

Behind the scenes the app identifies the game via a short LLM call, then kicks off polite background work: discovering the game's wiki, crawling pages (rate-limited so the wiki host isn't hammered), then building a per-game "quick reference" and a perception schema once at least one page has landed on disk. None of this blocks you — you can ask questions immediately. Whatever's not ready yet shows up as a `⚠` in the status row.

Only the currently-selected window's game has a crawler running. Switching windows cancels the old crawler; switching back resumes from the pages already on disk.

Fullscreen-exclusive DirectX games typically come out black; switch the game to **borderless windowed** or **windowed**.

### Captures
Three ways to capture, all of which save into the active session folder (`%USERPROFILE%\game_assistant\sessions\<timestamp>\`) as `shot_<timestamp>.png`:

- **Auto-timer** — fires every N seconds (default 60, configurable).
- **Capture button** — top-right of the window. Manual one-off.
- **Global hotkey** — `Ctrl+Alt+S` by default. Works even when the app isn't focused, so you can hit it mid-game.

### Asking the assistant
1. (Optional) Pick or create a **Goal** in the dropdown (persistent overview the assistant will use to shape its analysis — see "Goals" below).
2. Type your **question** — anything from "what should I do next turn?" to "what does this debuff do?" Press **Enter** to submit (Shift+Enter inserts a newline), or click the Submit button.

Each submit takes a fresh screenshot first, then composes the prompt from whatever ingredients exist *right now*: the most recent N images (default 5, configurable, capped at 20), your full prior Q&A history, the active goal if set, the per-game quick-reference if it's been built, and a perception synthesis from the per-game schema if that's been built. The local `search_game_rules` tool gives the assistant on-demand access to the crawled wiki — it's the only information tool the assistant uses for game questions. A blue banner shows elapsed time while waiting.

If background work hasn't produced an ingredient yet (no wiki, no quick-ref, no schema), the submit still goes through — the response just won't include that context. The status row tells you which ingredients are missing so you can interpret a thinner-than-usual answer.

The assistant replies in three labelled sections:
- **State** — what it sees on screen, with specific numbers/names from the UI.
- **Reasoning** — the game mechanics actually relevant to the decision.
- **Answer** — one concrete recommendation grounded in the reasoning.

### Correcting mid-conversation
If the assistant misreads the state on a given turn, just say so in your next message ("That bar in the bottom-left is mana, not HP — reanalyze"). The assistant revises its understanding for that turn forward. Mid-conversation game-name corrections aren't a path — if the app identified the wrong game and you care, edit the wiki URL for the active game in **Settings → Game knowledge sources**.

### Goals
Above the question box: a goal dropdown plus **+ New**, **Edit**, **Save**, **Delete** buttons. Goals are plain Markdown files at `~/game_assistant/goals/<name>.md`. Each holds a persistent campaign/build overview ("Skarbrand rush, Total War: WH3, ignore Cathay until turn 50…") that gets prepended to every request while it's selected.

- **+ New** prompts for a name, creates an empty file, drops you into edit mode.
- **Edit** unlocks the text area.
- **Save** writes the buffer to disk and locks the text area again.
- **Delete** removes the .md file.

The active goal is preserved across launches. Select `(none)` to send requests without any goal. If you switch goals (or close the app) with unsaved edits, you'll be prompted before discarding.

### Sessions
Each app launch creates a new session folder. The **New session** button starts a fresh folder *and* clears the in-memory Q&A history. Old session folders stay on disk; you can browse them like normal screenshots.

### Settings (`Settings…` button in the top bar)
All settings persist across launches.

**General:**
- **Reasoning model** — `claude-sonnet-4-6` (default, balanced) / `claude-opus-4-7` (slowest, best reasoning) / `claude-opus-4-6` / `claude-haiku-4-5-…` (fastest, cheapest).
- **Capture interval** — 5–3600 seconds.
- **Last N images sent** — 1–20 (Anthropic vision API cap). Bigger N = more context but more tokens billed.
- **Global hotkey** — type a chord like `Ctrl+Alt+S`.
- **Prompt cache** — caches the system prompt across requests.

**Game knowledge sources:** one card per game the app has seen. Edit the wiki URL/API URL/root page to point at a different wiki (saving while that game is active restarts the crawler; saving while another game is active just persists). Delete the corpus to wipe local pages, quick-ref, and schema. The app uses the server-side web-search tool *only* during initial wiki discovery — not during normal answering.

**Perception schema:** the per-game schema that names the slots stage-1 perception fills. Edit/save the markdown, or regenerate from the latest quick-ref.

**Advanced:** model overrides for each stage (game ID, wiki discovery, perception stage-1, perception stage-2, quick-ref, schema-builder), wiki user-agent and rate limit, tool-use iteration cap. Defaults are reasonable; touch only if you know why.

---

## Known limitations (v1)

- **Windows only.** Uses `win32gui` for window enumeration and Windows Credential Manager for the API key.
- **No fullscreen-exclusive games.** Switch the game to borderless windowed.
- **No streaming or cancel.** Each submit blocks the Submit button until response or 120s timeout.
- **No conversation export.** History lives in memory; closed app or New Session wipes it.
- **No multi-monitor DPI weirdness handling beyond `PER_MONITOR_DPI_AWARE_V2`.** Should be fine on standard setups.

---

## Troubleshooting

- **Status row shows "⚠ window unreachable"** — The selected window is minimized or being drawn off-screen. The capture timer keeps ticking; the moment the window is reachable again, captures resume and the warning clears automatically. No need to re-pick.
- **Status row shows "⚠ no wiki"** — Wiki discovery couldn't find a community wiki for this game. Submits still work; the prompt just won't include wiki context. Paste a wiki URL manually under **Settings → Game knowledge sources** to kick off a crawl.
- **Status row shows "⚠ no quick-ref" / "⚠ no schema"** — Background builds haven't produced these yet (crawl still in progress, or hadn't reached any pages when the app last ran). Submits work without them; responses just won't include the perception synthesis.
- **API errors in red in the Chat panel** — Full error and traceback are shown. Common causes: invalid API key, no network, insufficient credits, request timeout.
- **App hangs longer than 3 minutes** — Check `~/game_assistant/logs/run.log`. Reasoning has a 180s timeout; stage-1 and stage-2 perception each have 120s.
- **Hotkey doesn't fire** — Some games capture all input. Try alt-tabbing out, or change to a less common chord under **Settings…**.

---

## For developers

### Architecture

The exe is a thin native shell. It boots a local FastAPI server (`uvicorn`) on a random localhost port in-process, then renders the web UI (`app/static/`) inside a chromeless desktop window via `pywebview` + Edge WebView2. There is no separate server to manage and nothing is exposed off-machine. From the user's perspective it looks like a regular desktop app — no browser tab, no URL bar.

### Run from source

Requires Python 3.13+.

```powershell
git clone https://github.com/tristan00/game_assistant.git
cd game_assistant

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python main.py            # default: native desktop window (same UX as the released exe)
python main.py --web      # headless/dev mode (currently broken — see note below)
```

> **Note**: `--web` mode currently doesn't work end-to-end and is not the focus of active development. Use the native mode (`python main.py` with no flag) for both regular use and dev iteration. The frontend lives at `app/static/` and can also be opened directly against a running native session if you need devtools.

### Build the exe locally

Normally CI does this for you on every push to `main` (see below). To build it yourself:

```powershell
pyinstaller game_assistant.spec
```

Output: `dist\game_assistant.exe` — single file, no console window.

### Tests

```powershell
pytest -v
```

Covers `image_utils`, `session`, `goals`, `settings`, `config` (mocked keyring), `prompts`, `assistant_client` (mocked Anthropic client), the `bump_version` script, and the `web_server` REST + WebSocket surface (via FastAPI TestClient). Capture/hotkey/pywebview edges are deferred — they need a real OS surface and are best validated by running the app.

### CI / release pipeline

`.github/workflows/ci.yml` runs on every push and PR.

- **test job**: pytest on `windows-latest`.
- **release job** (only on push to `main`, after test passes):
  1. Reads a bump segment from the head commit message: `[bump:major]`, `[bump:minor]`, or default `patch`.
  2. Bumps `__version__` in `app/__init__.py` via `scripts/bump_version.py`.
  3. Tags the commit as `vX.Y.Z`.
  4. Runs `pyinstaller game_assistant.spec`.
  5. Pushes the bump commit + tag back to `main`.
  6. Publishes a GitHub Release with `dist/game_assistant.exe` attached.

To skip releasing for a specific push, include `[skip release]` in the commit message — the bump-back commit uses this marker itself so it doesn't loop.
