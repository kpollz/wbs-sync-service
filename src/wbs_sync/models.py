"""Data models: WBS records, the slim (LangFlow) representation, departments, and sync state."""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict

# IDs from the WBS API may arrive as int or str depending on the source —
# accept either. We don't use these ids downstream (the slim record drops them).
IdValue = Union[int, str]


class _NamedRef(BaseModel):
    """A nested {id, name} reference (e.g. workCategory, job)."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[IdValue] = None
    name: Optional[str] = None


class WorkCode(BaseModel):
    """Full WBS record as returned by /api/works/search and /api/work-profiles."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[IdValue] = None
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None
    task: Optional[str] = None
    workCategory: Optional[_NamedRef] = None
    job: Optional[_NamedRef] = None
    createdBy: Optional[str] = None
    updatedBy: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class Department(BaseModel):
    """A part/department from /api/departments (we only use `name`)."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[IdValue] = None
    code: Optional[str] = None
    name: Optional[str] = None
    createdDate: Optional[str] = None


class WorkCodeSlim(BaseModel):
    """Minimal record pushed to LangFlow — only what the agent needs.

    workCategory/job are flattened to their name (a plain string).
    """

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None
    task: Optional[str] = None
    workCategory: Optional[str] = None
    job: Optional[str] = None


class TargetState(BaseModel):
    """Per-target persistent state (one entry per sync target)."""

    langflow_name: Optional[str] = None
    last_hash: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_attempted_at: Optional[str] = None
    langflow_file_id: Optional[str] = None
    langflow_path: Optional[str] = None
    record_count: Optional[int] = None
    last_status: Optional[str] = None  # "success" | "failed" | "error"
    last_error: Optional[str] = None


class State(BaseModel):
    """Persistent sync state, stored as data/state.json (one target dict)."""

    targets: dict[str, TargetState] = {}
    last_run_at: Optional[str] = None
    departments: list[str] = []  # audit: department names seen on the last run


class SyncResult(BaseModel):
    """Outcome of syncing a single target."""

    changed: bool
    record_count: int
    uploaded: bool = False
    file_id: Optional[str] = None
    attempts: int = 0
    error: Optional[str] = None


class RunResult(BaseModel):
    """Aggregate outcome of one run_once() across all targets."""

    targets: int = 0
    changed: int = 0
    uploaded: int = 0
    failed: int = 0
    removed: int = 0
