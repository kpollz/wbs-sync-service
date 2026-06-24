from pathlib import Path

import pytest

from wbs_sync.langflow_client import LangFlowClient, LangFlowError


def _client():
    return LangFlowClient("http://lf/", "key", file_name="wbs", timeout=5)


def test_list_files_bare_list(requests_mock):
    requests_mock.get(
        "http://lf/api/v2/files",
        json=[{"id": "1", "name": "wbs"}],
    )
    files = _client().list_files()
    assert files == [{"id": "1", "name": "wbs"}]


def test_list_files_wrapped_dict(requests_mock):
    requests_mock.get(
        "http://lf/api/v2/files",
        json={"files": [{"id": "1", "name": "wbs"}]},
    )
    files = _client().list_files()
    assert files == [{"id": "1", "name": "wbs"}]


def test_upload_returns_meta(requests_mock, tmp_path):
    path = tmp_path / "wbs.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.post(
        "http://lf/api/v2/files",
        json={"id": "new", "name": "wbs", "path": "USER/new.json", "size": 2},
    )
    meta = _client().upload_file(path)
    assert meta["id"] == "new"
    assert meta["path"] == "USER/new.json"


def test_upload_error_raises(requests_mock, tmp_path):
    path = tmp_path / "wbs.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.post("http://lf/api/v2/files", status_code=500, text="boom")
    with pytest.raises(LangFlowError):
        _client().upload_file(path)


def test_replace_file_deletes_matches_then_uploads(requests_mock, tmp_path):
    path = tmp_path / "wbs.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.get(
        "http://lf/api/v2/files",
        json=[
            {"id": "old1", "name": "wbs"},
            {"id": "old2", "name": "wbs"},
            {"id": "keep", "name": "other-doc"},
        ],
    )
    requests_mock.delete("http://lf/api/v2/files/old1", json={"deleted": True})
    requests_mock.delete("http://lf/api/v2/files/old2", json={"deleted": True})
    requests_mock.post(
        "http://lf/api/v2/files",
        json={"id": "new", "name": "wbs", "path": "USER/new.json", "size": 2},
    )

    meta = _client().replace_file(path)

    assert meta["id"] == "new"
    deleted_ids = {
        Path(req.url).name
        for req in requests_mock.request_history
        if req.method == "DELETE"
    }
    assert deleted_ids == {"old1", "old2"}  # 'keep' untouched
    assert sum(1 for r in requests_mock.request_history if r.method == "POST") == 1


def test_replace_file_no_existing_files(requests_mock, tmp_path):
    path = tmp_path / "wbs.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.get("http://lf/api/v2/files", json=[])
    requests_mock.post(
        "http://lf/api/v2/files",
        json={"id": "new", "name": "wbs", "path": "USER/new.json", "size": 2},
    )
    meta = _client().replace_file(path)
    assert meta["id"] == "new"
    assert sum(1 for r in requests_mock.request_history if r.method == "DELETE") == 0
