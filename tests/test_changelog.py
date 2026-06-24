from wbs_sync import changelog


def test_append_creates_file_and_lines(tmp_path):
    path = tmp_path / "changelog.jsonl"
    changelog.append(path, {"status": "success", "n": 1})
    changelog.append(path, {"status": "failed", "n": 2})

    entries = changelog.read(path)
    assert len(entries) == 2
    assert entries[0]["status"] == "success"
    assert entries[1]["n"] == 2


def test_append_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "changelog.jsonl"
    changelog.append(path, {"status": "success"})
    assert path.exists()
    assert changelog.read(path)[0]["status"] == "success"


def test_read_missing_returns_empty(tmp_path):
    assert changelog.read(tmp_path / "nope.jsonl") == []


def test_read_skips_corrupt_lines(tmp_path):
    path = tmp_path / "changelog.jsonl"
    path.write_text(
        '{"status": "success"}\n'
        "this is not json\n"
        "\n"
        '{"status": "failed"}\n',
        encoding="utf-8",
    )
    entries = changelog.read(path)
    assert [e["status"] for e in entries] == ["success", "failed"]
