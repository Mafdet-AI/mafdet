"""The providers' API keys: where the agent finds them and where it keeps one you type in.

In order: the provider's environment variable (it always wins, and is never copied anywhere), then the
system's credential store through `keyring` (Windows Credential Manager, the macOS Keychain, the Secret
Service on Linux), under the service "mafdet" and the provider's name. A key typed in `/model` goes there,
encrypted by the system and never into a file of MAFDET's; where no store is available it lives only for
the session.
"""
from __future__ import annotations

import os

SERVICE = "mafdet"
_session: dict[str, str] = {}


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as Fail
    except ImportError:
        return None
    try:
        return None if isinstance(keyring.get_keyring(), Fail) else keyring
    except Exception:
        return None


def get(name: str, env: tuple[str, ...] = ()) -> tuple[str, str]:
    """(key, where it came from: the variable's name, "store", "session", or "" with no key)."""
    for var in env:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip(), var
    store = _keyring()
    if store is not None:
        try:
            key = store.get_password(SERVICE, name)
        except Exception:
            key = None
        if key:
            return key, "store"
    if _session.get(name):
        return _session[name], "session"
    return "", ""


def put(name: str, key: str) -> str:
    """Keep `key` for provider `name`: "store" when the system keeps it, "session" when only this run does."""
    key = key.strip()
    _session[name] = key
    store = _keyring()
    if store is not None:
        try:
            store.set_password(SERVICE, name, key)
            return "store"
        except Exception:
            pass
    return "session"


def forget(name: str):
    _session.pop(name, None)
    store = _keyring()
    if store is not None:
        try:
            store.delete_password(SERVICE, name)
        except Exception:
            pass
