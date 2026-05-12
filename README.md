# game_assistant

Windows desktop app that periodically screenshots a game window and lets you ask an AI assistant questions about what's happening on screen. Conversation continues across turns so you can correct the assistant's read of the situation and get a revised answer. Currently uses the Anthropic API; designed to be provider-agnostic in future revisions.

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
A dialog asks for your Anthropic API key. Paste it. You can dismiss the dialog and use the app for capture-only — AI requests will fail with a clear error until a key is set. Update or rotate the key any time via the **API key…** button in the top bar.

### Pick a game window
The dropdown lists open windows. Pick the game (or any application — works fine for browsers, video players, etc., as long as the window is not occluded or fullscreen-exclusive). The capture timer starts automatically the moment a window is selected.

Fullscreen-exclusive DirectX games typically come out black; switch the game to **borderless windowed** or **windowed**.

### Captures
Three ways to capture, all of which save into the active session folder (`%USERPROFILE%\game_assistant\sessions\<timestamp>\`) as `shot_<timestamp>.png`:

- **Auto-timer** — fires every N seconds (default 60, configurable).
- **Capture button** — top-right of the window. Manual one-off.
- **Global hotkey** — `Ctrl+Alt+S` by default. Works even when the app isn't focused, so you can hit it mid-game.

### Asking the assistant
1. (Optional) Pick or create a **Strategy** in the dropdown (persistent strategic overview the assistant will use to shape its analysis — see "Strategies" below).
2. Type your **question** — anything from "what should I do next turn?" to "what does this debuff do?" Press **Enter** to submit (Shift+Enter inserts a newline), or click the Submit button.

Each submit takes a fresh screenshot first, then sends the most recent N images (default 5, configurable, capped at 20) plus your full prior Q&A history to the assistant. A blue banner shows elapsed time while waiting.

The assistant replies in three labelled sections:
- **State** — what it sees on screen, with specific numbers/names from the UI.
- **Reasoning** — the game mechanics actually relevant to the decision.
- **Answer** — one concrete recommendation grounded in the reasoning.

### Correcting the assistant
If it misidentifies the game or misreads the state, just say so in your next message ("Actually that's PoE 2, not Diablo 4 — reanalyze"). The assistant revises its understanding for that turn forward.

### Strategies
Above the question box: a strategy dropdown plus **+ New**, **Edit**, **Save**, **Delete** buttons. Strategies are plain Markdown files at `~/game_assistant/strategies/<name>.md`. Each holds a persistent campaign/build overview ("Skarbrand rush, Total War: WH3, ignore Cathay until turn 50…") that gets prepended to every request while it's selected.

- **+ New** prompts for a name, creates an empty file, drops you into edit mode.
- **Edit** unlocks the text area.
- **Save** writes the buffer to disk and locks the text area again.
- **Delete** removes the .md file.

The active strategy is preserved across launches. Select `(none)` to send requests without any strategy. If you switch strategies (or close the app) with unsaved edits, you'll be prompted before discarding.

### Sessions
Each app launch creates a new session folder. The **New session** button starts a fresh folder *and* clears the in-memory Q&A history. Old session folders stay on disk; you can browse them like normal screenshots.

### Settings (`Settings…` button in the top bar)
All settings persist across launches:
- **Model** — `claude-sonnet-4-6` (default, balanced) / `claude-opus-4-7` (slowest, best reasoning) / `claude-opus-4-6` / `claude-haiku-4-5-…` (fastest, cheapest).
- **Capture interval** — 5–3600 seconds.
- **Last N images sent** — 1–20 (Anthropic vision API cap). Bigger N = more context but more tokens billed.
- **Web searches per request (max)** — 0–10. 0 disables the server-side web search tool entirely.
- **Global hotkey** — type a chord like `Ctrl+Alt+S`.

---

## Known limitations (v1)

- **Windows only.** Uses `win32gui` for window enumeration and Windows Credential Manager for the API key.
- **No fullscreen-exclusive games.** Switch the game to borderless windowed.
- **No streaming or cancel.** Each submit blocks the Submit button until response or 120s timeout.
- **No conversation export.** History lives in memory; closed app or New Session wipes it.
- **No multi-monitor DPI weirdness handling beyond `PER_MONITOR_DPI_AWARE_V2`.** Should be fine on standard setups.

---

## Troubleshooting

- **"Capture failed — timer stopped"** — The window was probably closed. Pick another window from the dropdown.
- **API errors in red in the Chat panel** — Full error and traceback are shown. Common causes: invalid API key, no network, insufficient credits.
- **App hangs longer than 2 minutes** — Check `~/game_assistant/logs/run.log`. The 120s request timeout should surface an error in the log if the API call is the cause.
- **Hotkey doesn't fire** — Some games capture all input. Try alt-tabbing out, or change to a less common chord under **Settings…**.

---

## For developers

### Architecture

The exe is a thin native shell. It boots a local FastAPI server (`uvicorn`) on a random localhost port in-process, then renders the web UI (`app/static/`) inside a chromeless desktop window via `pywebview` + Edge WebView2. There is no separate server to manage and nothing is exposed off-machine. From the user's perspective it looks like a regular desktop app — no browser tab, no URL bar.

### Run from source

Requires Python 3.11+.

```powershell
git clone https://github.com/tristan00/game_assistant.git
cd game_assistant

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python main.py            # default: native desktop window (same UX as the released exe)
python main.py --web      # headless/dev mode: serves http://127.0.0.1:8765 and opens your system browser (useful for devtools-driven iteration on the HTML/CSS/JS)
```

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

Covers `image_utils`, `session`, `strategies`, `settings`, `config` (mocked keyring), `prompts`, `assistant_client` (mocked Anthropic client), the `bump_version` script, and the `web_server` REST + WebSocket surface (via FastAPI TestClient). Capture/hotkey/pywebview edges are deferred — they need a real OS surface and are best validated by running the app.

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
