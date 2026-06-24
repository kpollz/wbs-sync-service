"""Data models for WBS records, the slim (LangFlow) representation, and sync state."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class _NamedRef(BaseModel):
    """A nested {id, name} reference (e.g. workCategory, job)."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None


class WorkCode(BaseModel):
    """Full WBS record as returned by /api/works/search."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
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


class State(BaseModel):
    """Persistent sync state, stored as data/state.json."""

    last_hash: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_attempted_at: Optional[str] = None
    langflow_file_id: Optional[str] = None
    langflow_path: Optional[str] = None
    record_count: Optional[int] = None
    last_status: Optional[str] = None  # "success" | "failed"
    last_error: Optional[str] = None


class SyncResult(BaseModel):
    """Outcome of a single run_once() invocation."""

    changed: bool
    record_count: int
    uploaded: bool = False
    file_id: Optional[str] = None
    attempts: int = 0
    error: Optional[str] = None
