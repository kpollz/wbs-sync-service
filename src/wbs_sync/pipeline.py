"""End-to-end sync pipeline.

Flow per tick:
  1. fetch WBS -> transform to slim dicts
  2. write candidate to a TEMP file (the temp file exists ONLY to compare)
  3. compare candidate vs the persisted 'newest' file
     - unchanged -> delete temp, skip
     - changed   -> promote temp -> newest IMMEDIATELY, then upload to LangFlow
  4. write changelog + state regardless of upload outcome

Only one data file ever exists at rest (the 'newest'); the temp file is always cleaned up.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import change_detector, changelog, state as state_store, transformer, wbs_client
from .config import Settings, get_settings
from .langflow_client import LangFlowClient
from .models import State, SyncResult
from .wbs_client import WBSClient

log = logging.getLogger(__name__)


def _write_json_atomic(path: Path, data: list[dict[str, Any]]) -> None:
    """Write the list (sorted by code) as pretty JSON, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(data, key=lambda r: (r.get("code") or "", r.get("name") or ""))
    tmp = path.with_name(path.name + ".swap")
    tmp.write_text(
        json.dumps(ordered, sort_keys=True, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _upload_with_retry(
    langflow: LangFlowClient,
    path: Path,
    max_retries: int,
    backoff: float,
) -> dict[str, Any]:
    """Upload via langflow.replace_file, retrying on failure (linear backoff)."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            meta = langflow.replace_file(path)
            return {"success": True, "attempts": attempt, "meta": meta, "error": None}
        except Exception as exc:  # network / LangFlowError / etc.
            last_error = exc
            log.warning("upload attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(backoff * attempt)
    return {"success": False, "attempts": max_retries, "meta": None, "error": str(last_error)}


def run_once(
    *,
    force: bool = False,
    settings: Settings | None = None,
    wbs: WBSClient | None = None,
    langflow: LangFlowClient | None = None,
) -> SyncResult:
    """Run one full sync cycle (see module docstring)."""
    cfg = settings or get_settings()
    wbs = wbs or WBSClient(cfg.wbs_base_url, cfg.wbs_api_key, cfg.wbs_page_size, cfg.http_timeout)
    langflow = langflow or LangFlowClient(
        cfg.langflow_base_url, cfg.langflow_api_key, cfg.langflow_file_name, cfg.http_timeout
    )

    # 1. Fetch + transform
    records = wbs.fetch_all()
    slim = transformer.to_slim_list(records)
    new_dicts = [s.model_dump() for s in slim]

    # 2. Write candidate to the temp file
    temp_path = cfg.temp_file
    _write_json_atomic(temp_path, new_dicts)

    # 3. Compare candidate vs the persisted newest file
    newest_path = cfg.data_file
    old_dicts = _read_json_list(newest_path)
    new_hash = change_detector.compute_hash(new_dicts)
    old_hash = change_detector.compute_hash(old_dicts)
    changed = force or new_hash != old_hash

    if not changed:
        temp_path.unlink(missing_ok=True)
        log.info("no change (hash=%s); skipping sync", new_hash[:12])
        return SyncResult(changed=False, record_count=len(new_dicts))

    # 4. Changed (or forced): promote temp -> newest IMMEDIATELY (newest always
    #    reflects the latest data), then upload to LangFlow with retry.
    diff = change_detector.compute_diff(old_dicts, new_dicts)
    os.replace(temp_path, newest_path)  # consumes the temp file; newest is now the new data

    outcome = _upload_with_retry(
        langflow, newest_path, cfg.sync_max_retries, cfg.sync_retry_backoff
    )

    ts = datetime.now(timezone.utc).isoformat()
    meta = outcome["meta"] or {}
    changelog.append(
        cfg.changelog_file,
        {
            "ts": ts,
            "status": "success" if outcome["success"] else "failed",
            "forced": force,
            "record_count": len(new_dicts),
            "diff": diff,
            "attempts": outcome["attempts"],
            "max_retries": cfg.sync_max_retries,
            "langflow_file_id": meta.get("id"),
            "langflow_path": meta.get("path"),
            "error": outcome["error"],
        },
    )

    # newest already holds the new data, so last_hash always advances.
    # last_synced_at advances only on a successful upload.
    prev = state_store.load(cfg.state_file)
    state_store.save(
        prev.model_copy(
            update={
                "last_hash": new_hash,
                "last_synced_at": ts if outcome["success"] else prev.last_synced_at,
                "last_attempted_at": ts,
                "langflow_file_id": meta.get("id") or prev.langflow_file_id,
                "langflow_path": meta.get("path") or prev.langflow_path,
                "record_count": len(new_dicts),
                "last_status": "success" if outcome["success"] else "failed",
                "last_error": outcome["error"],
            }
        ),
        cfg.state_file,
    )

    if outcome["success"]:
        log.info(
            "synced %d records to LangFlow (added=%d updated=%d removed=%d, attempts=%d)",
            len(new_dicts), diff["added"], diff["updated"], diff["removed"], outcome["attempts"],
        )
        return SyncResult(
            changed=True,
            record_count=len(new_dicts),
            uploaded=True,
            file_id=meta.get("id"),
            attempts=outcome["attempts"],
        )

    log.error("upload failed after %d attempts: %s", outcome["attempts"], outcome["error"])
    return SyncResult(
        changed=True,
        record_count=len(new_dicts),
        uploaded=False,
        attempts=outcome["attempts"],
        error=outcome["error"],
    )
