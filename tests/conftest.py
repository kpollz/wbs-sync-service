"""Shared fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from wbs_sync.config import Settings


def make_workcode(**over) -> dict:
    """A full WBS record as a raw dict (mirrors the API response)."""
    base = dict(
        id="1",
        name="Design module",
        code="WBS-001",
        description="Thiết kế module",
        input="yêu cầu",
        output="bản vẽ",
        task="vẽ",
        workCategory={"id": "wc1", "name": "Engineering"},
        job={"id": "j1", "name": "Designer"},
        createdBy="alice",
        updatedBy="bob",
        createdAt="2026-01-01T00:00:00Z",
        updatedAt="2026-06-01T00:00:00Z",
    )
    base.update(over)
    return base


class FakeWBS:
    """In-memory WBS client: departments + centralized works + per-part profiles."""

    def __init__(self, departments=None, works=None, profiles=None):
        self.departments = departments or []  # list[Department]
        self.works = works or []  # list[WorkCode]
        self.profiles = profiles or {}  # dict[department_name -> list[WorkCode]]

    def fetch_departments(self):
        return self.departments

    def fetch_works(self):
        return self.works

    def fetch_work_profiles(self, department_name):
        return self.profiles.get(department_name, [])


class FakeLangFlow:
    """In-memory LangFlow client tracking uploaded filenames and deleted bases."""

    def __init__(self, meta=None, fail=False):
        self.meta = meta
        self.fail = fail
        self.replace_calls = []  # list of uploaded filenames
        self.deleted_bases = []  # list of base names passed to delete_by_base
        self._n = 0

    def replace_file(self, path, filename):
        self.replace_calls.append(filename)
        if self.fail:
            raise RuntimeError("upload failed")
        self._n += 1
        base = Path(filename).stem
        return self.meta or {
            "id": f"id-{self._n}",
            "name": base,
            "path": f"USER/id-{self._n}.json",
            "size": 10,
        }

    def delete_by_base(self, base):
        self.deleted_bases.append(base)


@pytest.fixture
def workcode():
    return make_workcode


@pytest.fixture
def fake_wbs():
    return FakeWBS


@pytest.fixture
def fake_langflow():
    return FakeLangFlow


@pytest.fixture
def make_settings(tmp_path):
    def _factory(**over) -> Settings:
        defaults = dict(
            wbs_base_url="http://wbs",
            wbs_api_key="k",
            langflow_base_url="http://lf",
            langflow_api_key="k",
            state_dir=str(tmp_path),
        )
        defaults.update(over)
        return Settings(**defaults)

    return _factory
