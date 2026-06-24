from pathlib import Path

from wbs_sync import state as state_store
from wbs_sync.models import State


def test_load_missing_file_returns_empty_state(tmp_path):
    s = state_store.load(tmp_path / "state.json")
    assert s.last_hash is None
    assert s.record_count is None


def test_load_corrupt_file_returns_empty_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    s = state_store.load(path)
    assert s.last_hash is None


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    original = State(
        last_hash="abc123",
        last_synced_at="2026-06-24T10:00:00+00:00",
        langflow_file_id="file-1",
        langflow_path="USER/file-1.json",
        record_count=42,
    )
    state_store.save(original, path)
    loaded = state_store.load(path)
    assert loaded.last_hash == "abc123"
    assert loaded.record_count == 42
    assert loaded.langflow_path == "USER/file-1.json"


def test_save_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "state.json"
    state_store.save(State(last_hash="x"), path)
    assert path.exists()


def test_save_does_not_leave_temp_file(tmp_path):
    path = tmp_path / "state.json"
    state_store.save(State(last_hash="x"), path)
    assert not (tmp_path / "state.json.tmp").exists()
