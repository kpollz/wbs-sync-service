"""Atomic read/write of the persistent sync state (data/state.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import State


def load(path: Path) -> State:
    """Load state; returns an empty State() if the file does not exist."""
    if not path.exists():
        return State()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt state — treat as fresh rather than crashing the run.
        return State()
    return State(**data)


def save(state: State, path: Path) -> None:
    """Write state atomically (write temp + os.replace) to survive crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
