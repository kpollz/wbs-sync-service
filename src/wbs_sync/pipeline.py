"""End-to-end sync pipeline over N+1 targets (1 default + 1 per department).

Per target:
  1. fetch work codes -> transform to slim dicts
  2. write candidate to a TEMP file (the temp file exists ONLY to compare)
  3. compare candidate vs the target's persisted 'newest' file
     - unchanged -> delete temp, skip
     - changed   -> promote temp -> newest IMMEDIATELY, then upload to LangFlow
  4. write changelog (tagged with the target) + update per-target state

Departments that disappear from /api/departments have their LangFlow file
auto-deleted (unless the department list looks glitchy/empty).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import change_detector, changelog, naming, state as state_store, transformer
from .config import Settings, get_settings
from .langflow_client import LangFlowClient
from .models import Department, RunResult, State, SyncResult, TargetState, WorkCode
from .wbs_client import WBSClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncTarget:
    """One sync unit: a default file or a per-part file."""

    key: str  # "default" | "part:<slug>"
    label: str  # human-readable (department name or "default")
    langflow_name: str  # base name on LangFlow (no extension)

    @property
    def filename(self) -> str:
        return f"{self.langflow_name}.json"


def build_targets(cfg: Settings, wbs: WBSClient, departments: list[Department]) -> list[SyncTarget]:
    """Default target (centralized) + one target per department."""
    base = cfg.langflow_file_name
    targets: list[SyncTarget] = []

    if cfg.sync_default_enabled:
        targets.append(SyncTarget("default", "default", base))

    ordered = sorted(departments, key=lambda d: d.name or "")
    slugs = naming.assign_slugs([d.name or "" for d in ordered])
    for dept, slug in zip(ordered, slugs):
        original = dept.name or "unnamed"
        targets.append(
            SyncTarget(
                key=f"part:{slug}",
                label=original,
                langflow_name=f"{base}_{slug}",
            )
        )
    return targets


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
    filename: str,
    max_retries: int,
    backoff: float,
) -> dict[str, Any]:
    """Upload via langflow.replace_file, retrying on failure (linear backoff)."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            meta = langflow.replace_file(path, filename)
            return {"success": True, "attempts": attempt, "meta": meta, "error": None}
        except Exception as exc:
            last_error = exc
            log.warning("[%s] upload attempt %d/%d failed: %s", filename, attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(backoff * attempt)
    return {"success": False, "attempts": max_retries, "meta": None, "error": str(last_error)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_one(
    target: SyncTarget,
    *,
    fetch: Callable[[], list[WorkCode]],
    force: bool,
    cfg: Settings,
    langflow: LangFlowClient,
    state: State,
) -> SyncResult:
    """Sync a single target (fetch -> transform -> compare -> promote -> upload)."""
    newest_path = cfg.data_path_for(target.langflow_name)
    temp_path = cfg.temp_path_for(target.langflow_name)

    records = fetch()
    slim = transformer.to_slim_list(records)
    new_dicts = [s.model_dump() for s in slim]

    _write_json_atomic(temp_path, new_dicts)

    old_dicts = _read_json_list(newest_path)
    new_hash = change_detector.compute_hash(new_dicts)
    old_hash = change_detector.compute_hash(old_dicts)
    changed = force or new_hash != old_hash

    if not changed:
        temp_path.unlink(missing_ok=True)
        log.info("[%s] no change; skipping", target.label)
        return SyncResult(changed=False, record_count=len(new_dicts))

    diff = change_detector.compute_diff(old_dicts, new_dicts)
    os.replace(temp_path, newest_path)  # newest is now the new data

    outcome = _upload_with_retry(
        langflow, newest_path, target.filename, cfg.sync_max_retries, cfg.sync_retry_backoff
    )

    ts = _now()
    meta = outcome["meta"] or {}
    changelog.append(
        cfg.changelog_file,
        {
            "ts": ts,
            "target": target.key,
            "langflow_name": target.langflow_name,
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

    prev = state.targets.get(target.key, TargetState(langflow_name=target.langflow_name))
    state.targets[target.key] = prev.model_copy(
        update={
            "langflow_name": target.langflow_name,
            "last_hash": new_hash,
            "last_synced_at": ts if outcome["success"] else prev.last_synced_at,
            "last_attempted_at": ts,
            "langflow_file_id": meta.get("id") or prev.langflow_file_id,
            "langflow_path": meta.get("path") or prev.langflow_path,
            "record_count": len(new_dicts),
            "last_status": "success" if outcome["success"] else "failed",
            "last_error": outcome["error"],
        }
    )

    if outcome["success"]:
        log.info(
            "[%s] synced %d records (added=%d updated=%d removed=%d, attempts=%d)",
            target.label, len(new_dicts), diff["added"], diff["updated"], diff["removed"],
            outcome["attempts"],
        )
        return SyncResult(
            changed=True,
            record_count=len(new_dicts),
            uploaded=True,
            file_id=meta.get("id"),
            attempts=outcome["attempts"],
        )

    log.error("[%s] upload failed after %d attempts: %s", target.label, outcome["attempts"], outcome["error"])
    return SyncResult(
        changed=True,
        record_count=len(new_dicts),
        uploaded=False,
        attempts=outcome["attempts"],
        error=outcome["error"],
    )


def _cleanup_orphans(
    state: State,
    current_part_keys: set[str],
    langflow: LangFlowClient,
    cfg: Settings,
) -> int:
    """Delete LangFlow files for parts no longer in the department list. Returns count removed."""
    prior_part_keys = [k for k in state.targets if k.startswith("part:")]
    if not prior_part_keys:
        return 0

    # Guard: an empty current list while we previously had parts smells like an API glitch.
    if not current_part_keys:
        log.warning(
            "department list is empty but %d parts were managed; skipping orphan cleanup",
            len(prior_part_keys),
        )
        return 0

    removed = 0
    for key in prior_part_keys:
        if key in current_part_keys:
            continue
        target_state = state.targets[key]
        base = target_state.langflow_name or key.split(":", 1)[-1]
        try:
            langflow.delete_by_base(base)
        except Exception as exc:
            log.warning("orphan cleanup: delete failed for %s: %s", key, exc)
        changelog.append(
            cfg.changelog_file,
            {
                "ts": _now(),
                "target": key,
                "langflow_name": base,
                "status": "removed",
                "record_count": 0,
            },
        )
        del state.targets[key]
        removed += 1
        log.info("[%s] department gone; deleted LangFlow file '%s'", key, base)
    return removed


def run_once(
    *,
    force: bool = False,
    settings: Settings | None = None,
    wbs: WBSClient | None = None,
    langflow: LangFlowClient | None = None,
) -> RunResult:
    """Run one full sync cycle across all targets (default + per-part)."""
    cfg = settings or get_settings()
    wbs = wbs or WBSClient(
        cfg.wbs_base_url,
        cfg.wbs_api_key,
        cfg.wbs_page_size,
        cfg.http_timeout,
        cfg.wbs_works_path,
        cfg.wbs_departments_path,
        cfg.wbs_work_profiles_path,
    )
    langflow = langflow or LangFlowClient(cfg.langflow_base_url, cfg.langflow_api_key, cfg.http_timeout)

    state = state_store.load(cfg.state_file)

    departments = wbs.fetch_departments()
    targets = build_targets(cfg, wbs, departments)

    # Bind each target to its fetch callable (default vs per-part).
    fetchers: dict[str, Callable[[], list[WorkCode]]] = {"default": wbs.fetch_works}
    for t in targets:
        if t.key.startswith("part:"):
            name = t.label  # original department name
            fetchers[t.key] = lambda dn=name: wbs.fetch_work_profiles(dn)

    removed = _cleanup_orphans(state, {t.key for t in targets if t.key.startswith("part:")}, langflow, cfg)
    if removed:
        state_store.save(state, cfg.state_file)

    changed = uploaded = failed = 0
    for target in targets:
        try:
            res = _sync_one(
                target,
                force=force,
                fetch=fetchers[target.key],
                cfg=cfg,
                langflow=langflow,
                state=state,
            )
        except Exception:
            log.exception("[%s] sync crashed; marking as error", target.label)
            state.targets[target.key] = state.targets.get(
                target.key, TargetState(langflow_name=target.langflow_name)
            ).model_copy(update={"last_status": "error", "last_attempted_at": _now()})
            state_store.save(state, cfg.state_file)
            failed += 1
            continue

        if res.changed:
            changed += 1
            if res.uploaded:
                uploaded += 1
            else:
                failed += 1
        state_store.save(state, cfg.state_file)

    state.last_run_at = _now()
    state.departments = [d.name or "" for d in departments]
    state_store.save(state, cfg.state_file)

    log.info(
        "run done: targets=%d changed=%d uploaded=%d failed=%d removed=%d",
        len(targets), changed, uploaded, failed, removed,
    )
    return RunResult(
        targets=len(targets), changed=changed, uploaded=uploaded, failed=failed, removed=removed
    )
