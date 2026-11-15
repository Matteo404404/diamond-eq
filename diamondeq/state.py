"""Persistent settings: which profile belongs to which output, and switches."""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

DEFAULT_PORT = 8377
_lock = threading.Lock()


def path() -> Path:
    d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "diamondeq"
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.json"


def _defaults() -> dict:
    return {
        "enabled": True,
        "assignments": {},  # output key ("bt:<MAC>" or "node:<name>") -> profile id
        "labels": {},  # output key -> last seen name, so offline outputs stay readable
        "port": DEFAULT_PORT,
        "lan": True,  # let the phone sync over Wi-Fi (read-only, token protected)
        "token": secrets.token_urlsafe(24),
    }


def load() -> dict:
    with _lock:
        state = _defaults()
        try:
            state.update(json.loads(path().read_text()))
        except (OSError, ValueError):
            path().write_text(json.dumps(state, indent=2))
        return state


def update(**changes) -> dict:
    """Read-modify-write; dict values are merged key by key (None deletes a key)."""
    state = load()
    with _lock:
        for key, value in changes.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                for k, v in value.items():
                    if v is None:
                        state[key].pop(k, None)
                    else:
                        state[key][k] = v
            else:
                state[key] = value
        tmp = path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(path())
        return state


def mtime() -> float:
    try:
        return path().stat().st_mtime
    except OSError:
        return 0.0
