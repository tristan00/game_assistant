import pytest
from keyring.errors import PasswordDeleteError

from app import config


@pytest.fixture
def fake_keyring(monkeypatch):
    """Replace keyring.{get,set,delete}_password with an in-memory dict."""
    store: dict[tuple[str, str], str] = {}

    def fake_get(service, username):
        return store.get((service, username))

    def fake_set(service, username, password):
        store[(service, username)] = password

    def fake_delete(service, username):
        if (service, username) not in store:
            raise PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(config, "keyring", type("kr", (), {
        "get_password": staticmethod(fake_get),
        "set_password": staticmethod(fake_set),
        "delete_password": staticmethod(fake_delete),
    }))
    return store


def test_get_api_key_returns_none_when_absent(fake_keyring):
    assert config.get_api_key() is None


def test_set_then_get_returns_value(fake_keyring):
    config.set_api_key("sk-ant-test-key")
    assert config.get_api_key() == "sk-ant-test-key"


def test_set_overwrites_existing(fake_keyring):
    config.set_api_key("old")
    config.set_api_key("new")
    assert config.get_api_key() == "new"


def test_delete_removes_key(fake_keyring):
    config.set_api_key("k")
    config.delete_api_key()
    assert config.get_api_key() is None


def test_delete_when_absent_does_not_raise(fake_keyring):
    # The function swallows PasswordDeleteError by design.
    config.delete_api_key()  # no exception
