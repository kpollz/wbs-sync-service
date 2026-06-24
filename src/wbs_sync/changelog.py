"""Append-only JSONL changelog of real changes and upload outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append(path: Path, entry: dict[str, Any]) -> None:
    """Append one JSON object as a line. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read(path: Path) -> list[dict[str, Any]]:
    """Read all entries (skipping blank/corrupt lines). Handy for tests/audit."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
