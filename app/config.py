import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "game_assistant"
USERNAME = "anthropic_api_key"


def get_api_key() -> str | None:
    return keyring.get_password(SERVICE, USERNAME)


def set_api_key(key: str) -> None:
    keyring.set_password(SERVICE, USERNAME, key)


def delete_api_key() -> None:
    try:
        keyring.delete_password(SERVICE, USERNAME)
    except PasswordDeleteError:
        pass
