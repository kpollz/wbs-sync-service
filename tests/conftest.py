"""Shared fixtures for the test suite."""

from __future__ import annotations

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
    def __init__(self, records):
        self._records = records
        self.calls = 0

    def fetch_all(self):
        self.calls += 1
        return self._records


class FakeLangFlow:
    def __init__(self, meta=None, fail=False):
        self.meta = meta or {"id": "file-1", "name": "wbs", "path": "USER/file-1.json", "size": 10}
        self.fail = fail
        self.replace_calls = 0
        self.uploaded = []

    def replace_file(self, path):
        self.replace_calls += 1
        self.uploaded.append(path)
        if self.fail:
            raise RuntimeError("upload failed")
        return self.meta


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
