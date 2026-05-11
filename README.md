# game_assistant

Windows desktop app that periodically screenshots a game window and lets you ask Claude questions about what's happening on screen. Conversation continues across turns so you can correct Claude's read of the situation and get a revised answer.

---

## 1. Get an Anthropic API key

1. Sign in (or sign up) at <https://console.anthropic.com>.
2. Go to **Settings → API keys** (<https://console.anthropic.com/settings/keys>).
3. Click **Create Key**, copy the value (starts with `sk-ant-…`). Anthropic only shows it once — paste it somewhere safe.
4. The first launch of the app will prompt you for this key and store it in **Windows Credential Manager**. It never gets written to disk inside the project, and it does not get bundled into the `.exe`.

Vision API requests are billed against your Anthropic account. Pricing: <https://www.anthropic.com/pricing>.

---

## 2. Build the `.exe`

Requirements: **Windows 10/11**, **Python 3.11+**.

```powershell
git clone <this-repo> game_assistant
cd game_assistant

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

pyinstaller game_assistant.spec
```

Output: `dist\game_assistant.exe` (~60 MB, single file, no console window). Double-click to run; no Python needed on the target machine.

Logs (helpful when something misbehaves on a console-less .exe): `%USERPROFILE%\game_assistant\logs\run.log`.

To run from source without building the .exe:

```powershell
pip install -r requirements.txt
python main.py
```

---

## 3. Using the app

### First launch
A dialog asks for your Anthropic API key. Paste it. You can dismiss the dialog and use the app for capture-only — Claude requests will fail with a clear error until a key is set. Update or rotate the key any time via **Settings → Update API key…**.

### Pick a game window
The dropdown lists open windows. Pick the game (or any application — works fine for browsers, video players, etc., as long as the window is not occluded or fullscreen-exclusive). The capture timer starts automatically the moment a window is selected.

Fullscreen-exclusive DirectX games typically come out black; switch the game to **borderless windowed** or **windowed**.

### Captures
Three ways to capture, all of which save into the active session folder (`%USERPROFILE%\game_assistant\sessions\<timestamp>\`) as `shot_<timestamp>.png`:

- **Auto-timer** — fires every N seconds (default 60, configurable).
- **Capture button** — top-right of the window. Manual one-off.
- **Global hotkey** — `Ctrl+Alt+S` by default. Works even when the app isn't focused, so you can hit it mid-game.

### Asking Claude
1. (Optional) Type a **game context** hint — e.g. `Total War: Warhammer 3, Skarbrand campaign`. Claude will prefer your hint over its own identification and flag any contradictions.
2. Type your **question** — anything from "what should I do next turn?" to "what does this debuff do?"
3. Hit **Submit**.

Each submit takes a fresh screenshot first, then sends the most recent N images (default 5, configurable) plus your full prior Q&A history to Claude. A blue banner shows elapsed time while waiting.

Claude replies in three labelled sections:
- **State** — what it sees on screen, with specific numbers/names from the UI.
- **Reasoning** — the game mechanics actually relevant to the decision.
- **Answer** — one concrete recommendation grounded in the reasoning.

### Correcting Claude
If Claude misidentifies the game or misreads the state, just say so in your next message ("Actually that's PoE 2, not Diablo 4 — reanalyze"). Claude revises its understanding for that turn forward.

### Sessions
Each app launch creates a new session folder. The **New session** button starts a fresh folder *and* clears the in-memory Q&A history. Old session folders stay on disk; you can browse them like normal screenshots.

### Settings (`Settings → Preferences…`)
All four settings persist across launches:
- **Model** — `claude-sonnet-4-6` (default, balanced) / `claude-opus-4-7` (slowest, best reasoning) / `claude-opus-4-6` / `claude-haiku-4-5-…` (fastest, cheapest).
- **Capture interval** — 5–3600 seconds.
- **Last N images sent** — 1–50. Bigger N = more context but more tokens billed.
- **Global hotkey** — click the field and press a new chord.

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
- **API errors in red in the Q&A log** — Full error and traceback are shown. Common causes: invalid API key, no network, insufficient credits.
- **App hangs longer than 2 minutes** — Check `~/game_assistant/logs/run.log`. The 120s request timeout should surface an error in the log if the API call is the cause.
- **Hotkey doesn't fire** — Some games capture all input. Try alt-tabbing out, or change to a less common chord in Preferences.
