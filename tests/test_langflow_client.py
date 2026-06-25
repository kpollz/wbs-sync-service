from pathlib import Path

import pytest

from wbs_sync.langflow_client import LangFlowClient, LangFlowError


def _client():
    return LangFlowClient("http://lf/", "key", timeout=5)


def test_upload_uses_explicit_filename(requests_mock, tmp_path):
    path = tmp_path / "wbs_agent_knowledge.tmp.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.post(
        "http://lf/api/v2/files",
        json={"id": "x", "name": "wbs_agent_knowledge", "path": "USER/x.json"},
    )
    _client().upload_file(path, "wbs_agent_knowledge.json")
    body = requests_mock.last_request.body
    body = body.read() if hasattr(body, "read") else body
    assert b'filename="wbs_agent_knowledge.json"' in body


def test_replace_file_deletes_by_base_then_uploads(requests_mock, tmp_path):
    path = tmp_path / "wbs_agent_knowledge_qa.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.get(
        "http://lf/api/v2/files",
        json=[
            {"id": "old", "name": "wbs_agent_knowledge_qa"},
            {"id": "keep", "name": "wbs_agent_knowledge_sales"},
        ],
    )
    requests_mock.delete("http://lf/api/v2/files/old", json={})
    requests_mock.post(
        "http://lf/api/v2/files",
        json={"id": "new", "name": "wbs_agent_knowledge_qa", "path": "USER/new.json"},
    )
    meta = _client().replace_file(path, "wbs_agent_knowledge_qa.json")
    assert meta["id"] == "new"
    deleted = {Path(r.url).name for r in requests_mock.request_history if r.method == "DELETE"}
    assert deleted == {"old"}  # 'sales' untouched


def test_delete_by_base_matches_canonical_and_tmp_residue(requests_mock):
    requests_mock.get(
        "http://lf/api/v2/files",
        json=[
            {"id": "a", "name": "wbs_agent_knowledge_qa"},
            {"id": "b", "name": "wbs_agent_knowledge_qa.tmp"},
            {"id": "c", "name": "wbs_agent_knowledge_sales"},
        ],
    )
    requests_mock.delete("http://lf/api/v2/files/a", json={})
    requests_mock.delete("http://lf/api/v2/files/b", json={})
    requests_mock.delete("http://lf/api/v2/files/c", json={})
    _client().delete_by_base("wbs_agent_knowledge_qa")
    deleted = {Path(r.url).name for r in requests_mock.request_history if r.method == "DELETE"}
    assert deleted == {"a", "b"}  # canonical + tmp residue; 'sales' untouched


def test_replace_no_existing_files(requests_mock, tmp_path):
    path = tmp_path / "x.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.get("http://lf/api/v2/files", json=[])
    requests_mock.post(
        "http://lf/api/v2/files",
        json={"id": "new", "name": "x", "path": "USER/new.json"},
    )
    meta = _client().replace_file(path, "x.json")
    assert meta["id"] == "new"
    assert sum(1 for r in requests_mock.request_history if r.method == "DELETE") == 0


def test_upload_error_raises(requests_mock, tmp_path):
    path = tmp_path / "x.json"
    path.write_text("[]", encoding="utf-8")
    requests_mock.post("http://lf/api/v2/files", status_code=500, text="boom")
    with pytest.raises(LangFlowError):
        _client().upload_file(path, "x.json")
