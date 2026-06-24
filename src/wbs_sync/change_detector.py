"""Change detection: canonical hashing of file contents + structural diff.

The decision "did anything change?" is file-based: the candidate (temp file)
content is compared against the persisted 'newest' file content. ``compute_diff``
describes *what* changed, for the changelog.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_SEPARATORS = (",", ":")
_SAMPLE = 5


def _key(record: dict[str, Any]) -> str:
    return record.get("code") or record.get("name") or ""


def serialize(items: list[dict[str, Any]]) -> str:
    """Canonical string for a list of record dicts.

    Sorted by (code, name) and with sorted keys, so API-side reordering or
    dict-key order never produces a false "changed" signal.
    """
    ordered = sorted(items, key=lambda r: (_key(r),))
    return json.dumps(ordered, sort_keys=True, ensure_ascii=False, separators=_SEPARATORS)


def compute_hash(items: list[dict[str, Any]]) -> str:
    """SHA-256 hex of the canonical serialization."""
    return hashlib.sha256(serialize(items).encode("utf-8")).hexdigest()


def has_changed(new_hash: str, old_hash: str | None) -> bool:
    """True when there is no prior hash (first run) or the hash differs."""
    return old_hash is None or new_hash != old_hash


def _changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    return sorted(k for k in (set(old) | set(new)) if old.get(k) != new.get(k))


def compute_diff(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, Any]:
    """Structural diff keyed by code: what was added / updated / removed."""
    old_by = {_key(r): r for r in old}
    new_by = {_key(r): r for r in new}

    added = sorted(c for c in new_by if c not in old_by)
    removed = sorted(c for c in old_by if c not in new_by)
    updated_codes = sorted(
        c for c in new_by if c in old_by and new_by[c] != old_by[c]
    )
    unchanged = sum(1 for c in new_by if c in old_by and new_by[c] == old_by[c])

    return {
        "added": len(added),
        "removed": len(removed),
        "updated": len(updated_codes),
        "unchanged": unchanged,
        "total_new": len(new),
        "sample_added": added[:_SAMPLE],
        "sample_removed": removed[:_SAMPLE],
        "sample_updated": [
            {"code": c, "fields": _changed_fields(old_by[c], new_by[c])}
            for c in updated_codes[:_SAMPLE]
        ],
    }
