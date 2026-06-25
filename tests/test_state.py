from wbs_sync import state as state_store
from wbs_sync.models import State, TargetState


def test_load_missing_returns_empty_state(tmp_path):
    s = state_store.load(tmp_path / "state.json")
    assert s.targets == {}
    assert s.last_run_at is None
    assert s.departments == []


def test_load_corrupt_returns_empty_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert state_store.load(path).targets == {}


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    original = State(
        targets={
            "default": TargetState(
                langflow_name="wbs_agent_knowledge",
                last_hash="abc",
                last_status="success",
                record_count=5,
            ),
            "part:sales": TargetState(
                langflow_name="wbs_agent_knowledge_sales",
                last_hash="def",
                last_status="failed",
                last_error="boom",
            ),
        },
        departments=["Sales"],
        last_run_at="2026-06-25T00:00:00+00:00",
    )
    state_store.save(original, path)
    loaded = state_store.load(path)
    assert set(loaded.targets) == {"default", "part:sales"}
    assert loaded.targets["default"].last_hash == "abc"
    assert loaded.targets["part:sales"].last_status == "failed"
    assert loaded.targets["part:sales"].last_error == "boom"
    assert loaded.departments == ["Sales"]


def test_save_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "state.json"
    state_store.save(State(), path)
    assert path.exists()


def test_save_atomic_no_temp_left(tmp_path):
    path = tmp_path / "state.json"
    state_store.save(State(targets={"default": TargetState(last_hash="x")}), path)
    assert not (tmp_path / "state.json.tmp").exists()
