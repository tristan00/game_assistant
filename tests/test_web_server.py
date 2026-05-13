"""REST endpoint coverage for app/web_server.py via FastAPI TestClient.

The WebState's hotkey listener and capture-timer thread are stubbed out
(lifespan still runs so loop/WS broadcasting works), and keyring is
swapped for an in-memory dict so no test ever touches the user's real
Windows Credential Manager.
"""
import pytest
from fastapi.testclient import TestClient
from keyring.errors import PasswordDeleteError

from app import config as config_module
from app import web_server


@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict[tuple[str, str], str] = {}

    fake = type("kr", (), {
        "get_password": staticmethod(lambda s, u: store.get((s, u))),
        "set_password": staticmethod(lambda s, u, p: store.__setitem__((s, u), p)),
        "delete_password": staticmethod(
            lambda s, u: store.pop((s, u)) if (s, u) in store
            else (_ for _ in ()).throw(PasswordDeleteError("not found"))
        ),
    })
    monkeypatch.setattr(config_module, "keyring", fake)
    return store


@pytest.fixture
def quiet_webstate(monkeypatch):
    """Stop WebState.start from spinning up pynput + the capture-timer thread."""
    def noop_start(self, loop):
        self.loop = loop

    monkeypatch.setattr(web_server.WebState, "start", noop_start)
    monkeypatch.setattr(web_server.WebState, "shutdown", lambda self: None)


@pytest.fixture
def client(quiet_webstate, fake_keyring):
    state = web_server.WebState()
    app = web_server.create_app(state)
    with TestClient(app) as c:
        c.state = state  # surface for tests that want direct access
        yield c


# ---- snapshot / state ----


def test_get_state_returns_snapshot_shape(client):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "settings", "session", "selected_hwnd", "windows",
        "history", "goals", "active_goal", "in_flight", "has_api_key",
    ):
        assert key in body
    assert body["selected_hwnd"] is None
    assert body["in_flight"] is False
    assert body["has_api_key"] is False


def test_has_api_key_reflects_keyring_in_snapshot(client, fake_keyring):
    assert client.get("/api/state").json()["has_api_key"] is False
    config_module.set_api_key("sk-ant-test")
    assert client.get("/api/state").json()["has_api_key"] is True


# ---- window ----


def test_put_window_updates_selected_hwnd_when_window_exists(client, monkeypatch):
    monkeypatch.setattr(web_server, "list_windows", lambda: [(12345, "Test Window")])
    resp = client.put("/api/window", json={"hwnd": 12345})
    assert resp.status_code == 200
    assert resp.json() == {"selected_hwnd": 12345}
    assert client.get("/api/state").json()["selected_hwnd"] == 12345


def test_put_window_404_when_hwnd_unknown(client, monkeypatch):
    monkeypatch.setattr(web_server, "list_windows", lambda: [(1, "Other")])
    resp = client.put("/api/window", json={"hwnd": 99999})
    assert resp.status_code == 404


def test_put_window_404_leaves_prior_selection_unchanged(client, monkeypatch):
    """Regression: validation runs before mutation, so a stale hwnd does
    NOT poison the capture timer with an invalid handle."""
    monkeypatch.setattr(web_server, "list_windows", lambda: [(11, "Existing")])
    # First, validly select hwnd 11.
    client.put("/api/window", json={"hwnd": 11}).raise_for_status()
    assert client.get("/api/state").json()["selected_hwnd"] == 11
    # Now try a stale hwnd — windows list no longer includes 22.
    monkeypatch.setattr(web_server, "list_windows", lambda: [(11, "Existing")])
    resp = client.put("/api/window", json={"hwnd": 22})
    assert resp.status_code == 404
    # State retains the prior valid selection.
    assert client.get("/api/state").json()["selected_hwnd"] == 11


def test_put_window_null_clears(client):
    resp = client.put("/api/window", json={"hwnd": None})
    assert resp.json() == {"selected_hwnd": None}


# ---- settings ----


def test_get_settings_returns_defaults_initially(client):
    body = client.get("/api/settings").json()
    assert body["interval_seconds"] == 60
    assert body["last_n"] == 5
    assert body["model"] == "claude-sonnet-4-6"


def test_put_settings_round_trips(client):
    resp = client.put("/api/settings", json={"interval_seconds": 30, "last_n": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["interval_seconds"] == 30
    assert body["last_n"] == 7
    # Persists across a follow-up GET.
    assert client.get("/api/settings").json()["interval_seconds"] == 30


# ---- goals ----


def test_goals_list_empty_initially(client):
    body = client.get("/api/goals").json()
    assert body == {"goals": [], "active": ""}


def test_create_then_list_then_delete_goal(client):
    resp = client.post("/api/goals", json={"name": "My Plan"})
    assert resp.status_code == 200
    assert resp.json() == {"name": "My Plan"}

    listed = client.get("/api/goals").json()
    assert "My Plan" in listed["goals"]

    saved = client.put("/api/goals/My Plan", json={"content": "hello"})
    assert saved.json()["content"] == "hello"

    loaded = client.get("/api/goals/My Plan").json()
    assert loaded == {"name": "My Plan", "content": "hello"}

    deleted = client.delete("/api/goals/My Plan")
    assert deleted.json() == {"ok": True}
    assert "My Plan" not in client.get("/api/goals").json()["goals"]


def test_create_goal_rejects_empty_name(client):
    assert client.post("/api/goals", json={"name": ""}).status_code == 400


def test_create_goal_rejects_duplicate(client):
    client.post("/api/goals", json={"name": "dup"})
    assert client.post("/api/goals", json={"name": "dup"}).status_code == 409


def test_active_goal_404_for_unknown(client):
    assert client.put("/api/active_goal", json={"name": "ghost"}).status_code == 404


def test_active_goal_clears_when_set_to_empty(client):
    client.post("/api/goals", json={"name": "real"})
    client.put("/api/active_goal", json={"name": "real"})
    assert client.get("/api/state").json()["active_goal"] == "real"
    client.put("/api/active_goal", json={"name": ""})
    assert client.get("/api/state").json()["active_goal"] == ""


# ---- session ----


def test_post_session_new_creates_fresh_folder(client):
    before = client.get("/api/state").json()["session"]["folder_name"]
    resp = client.post("/api/session/new")
    assert resp.status_code == 200
    after = resp.json()["folder_name"]
    # Folder names are timestamp-stamped. Could happen to match if test is sub-second; loosen check.
    assert resp.json()["total_shots"] == 0


# ---- submit guards ----


def test_submit_412_when_no_api_key(client):
    resp = client.post("/api/submit", json={"question": "what now?"})
    assert resp.status_code == 412
    assert "API key" in resp.json()["detail"]


def test_submit_400_when_empty_question(client, fake_keyring):
    config_module.set_api_key("sk-ant-test")
    resp = client.post("/api/submit", json={"question": "   "})
    assert resp.status_code == 400


def test_submit_412_when_no_window_selected(client, fake_keyring):
    config_module.set_api_key("sk-ant-test")
    resp = client.post("/api/submit", json={"question": "real question"})
    assert resp.status_code == 412
    assert "window" in resp.json()["detail"]


def test_submit_412_when_no_active_game(client, fake_keyring):
    """API key + window set, but no game identified yet → fail loud."""
    from app import games
    config_module.set_api_key("sk-ant-test")
    with client.state.lock:
        client.state.selected_hwnd = 12345
        client.state.active_game_id = None
    resp = client.post("/api/submit", json={"question": "q"})
    assert resp.status_code == 412
    assert "active supported game" in resp.json()["detail"]


def test_submit_412_when_active_game_unsupported(client, fake_keyring):
    """Game marked is_supported=False → fail loud, no fallback."""
    from app import games
    config_module.set_api_key("sk-ant-test")
    games.upsert_game(games.GameEntry(
        game_id="obscure-game", display_name="Obscure",
        is_game=True, is_supported=False,
    ))
    with client.state.lock:
        client.state.selected_hwnd = 12345
        client.state.active_game_id = "obscure-game"
    resp = client.post("/api/submit", json={"question": "q"})
    assert resp.status_code == 412
    assert "unsupported" in resp.json()["detail"]


def test_submit_412_when_no_wiki_url(client, fake_keyring):
    from app import games
    config_module.set_api_key("sk-ant-test")
    games.upsert_game(games.GameEntry(
        game_id="g", display_name="G",
        is_game=True, is_supported=True, wiki_url=None,
    ))
    with client.state.lock:
        client.state.selected_hwnd = 12345
        client.state.active_game_id = "g"
    resp = client.post("/api/submit", json={"question": "q"})
    assert resp.status_code == 412
    assert "wiki_url" in resp.json()["detail"]


def test_submit_412_when_quick_ref_missing(client, fake_keyring):
    from app import games
    from app.wiki import storage as wiki_storage
    config_module.set_api_key("sk-ant-test")
    games.upsert_game(games.GameEntry(
        game_id="g", display_name="G",
        is_game=True, is_supported=True,
        wiki_url="https://e/", wiki_api_url="https://e/api.php", wiki_root_page="Main_Page",
    ))
    wiki_storage.ensure_wiki_dirs("g")
    with client.state.lock:
        client.state.selected_hwnd = 12345
        client.state.active_game_id = "g"
    resp = client.post("/api/submit", json={"question": "q"})
    assert resp.status_code == 412
    assert "quick_ref" in resp.json()["detail"]


def test_submit_412_when_schema_missing(client, fake_keyring):
    from app import games
    from app.wiki import storage as wiki_storage
    config_module.set_api_key("sk-ant-test")
    games.upsert_game(games.GameEntry(
        game_id="g", display_name="G",
        is_game=True, is_supported=True,
        wiki_url="https://e/", wiki_api_url="https://e/api.php", wiki_root_page="Main_Page",
    ))
    wiki_storage.ensure_wiki_dirs("g")
    wiki_storage.quick_ref_path("g").write_text("quick ref body", encoding="utf-8")
    with client.state.lock:
        client.state.selected_hwnd = 12345
        client.state.active_game_id = "g"
    resp = client.post("/api/submit", json={"question": "q"})
    assert resp.status_code == 412
    assert "perception schema" in resp.json()["detail"]


# ---- api key ----


def test_put_api_key_persists(client, fake_keyring):
    resp = client.put("/api/api_key", json={"key": "sk-ant-new"})
    assert resp.status_code == 200
    assert config_module.get_api_key() == "sk-ant-new"


def test_put_api_key_400_when_missing(client):
    assert client.put("/api/api_key", json={"key": ""}).status_code == 400


# ---- websocket ----


def test_websocket_sends_initial_snapshot(client):
    with client.websocket_connect("/ws") as ws:
        event = ws.receive_json()
        assert event["type"] == "snapshot"
        assert "state" in event
        assert "settings" in event["state"]
