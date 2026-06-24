import json

from wbs_sync import change_detector, changelog, state as state_store
from wbs_sync.models import WorkCode
from wbs_sync.pipeline import run_once


def _records(workcode, codes):
    return [WorkCode(**workcode(id=str(i), code=c)) for i, c in enumerate(codes, start=1)]


class FlakeyLangFlow:
    """Fails the first ``fail_times`` calls, then succeeds."""

    def __init__(self, fail_times, meta=None):
        self.fail_times = fail_times
        self.calls = 0
        self.meta = meta or {"id": "file-1", "name": "wbs", "path": "USER/file-1.json", "size": 10}

    def replace_file(self, path):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"transient {self.calls}")
        return self.meta


def _read_newest(settings):
    return json.loads(settings.data_file.read_text(encoding="utf-8"))


# --- happy path ---


def test_first_run_pushes_promotes_and_logs(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    wbs = fake_wbs(_records(workcode, ["WBS-001", "WBS-002"]))
    lf = fake_langflow()

    result = run_once(settings=settings, wbs=wbs, langflow=lf)

    assert result.changed is True and result.uploaded is True
    assert result.record_count == 2
    assert result.file_id == "file-1"
    assert lf.replace_calls == 1

    # temp consumed, newest promoted
    assert not settings.temp_file.exists()
    assert settings.data_file.exists()
    assert len(_read_newest(settings)) == 2

    # state + changelog reflect success
    state = state_store.load(settings.state_file)
    assert state.last_hash is not None
    assert state.langflow_file_id == "file-1"
    assert state.last_status == "success"

    entries = changelog.read(settings.changelog_file)
    assert len(entries) == 1
    assert entries[0]["status"] == "success"
    assert entries[0]["diff"]["added"] == 2


def test_unchanged_skips_upload_and_changelog(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    records = _records(workcode, ["WBS-001"])
    lf = fake_langflow()

    run_once(settings=settings, wbs=fake_wbs(records), langflow=lf)  # first: pushes
    result = run_once(settings=settings, wbs=fake_wbs(records), langflow=lf)  # second: no change

    assert result.changed is False
    assert lf.replace_calls == 1  # no new upload
    assert not settings.temp_file.exists()  # temp cleaned up
    assert len(changelog.read(settings.changelog_file)) == 1  # no new changelog line


def test_force_pushes_even_when_unchanged(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    records = _records(workcode, ["WBS-001"])
    lf = fake_langflow()

    run_once(settings=settings, wbs=fake_wbs(records), langflow=lf)
    result = run_once(force=True, settings=settings, wbs=fake_wbs(records), langflow=lf)

    assert result.changed is True and result.uploaded is True
    assert lf.replace_calls == 2
    entries = changelog.read(settings.changelog_file)
    assert len(entries) == 2
    assert entries[1]["forced"] is True
    assert entries[1]["diff"]["added"] == 0  # no real change


# --- failure + retry ---


def test_failure_keeps_newest_and_logs_failed(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings(sync_max_retries=1, sync_retry_backoff=0)

    # Seed a successful prior sync (newest file + state hash = OLD).
    run_once(
        settings=settings,
        wbs=fake_wbs(_records(workcode, ["WBS-001"])),
        langflow=fake_langflow(),
    )
    before = state_store.load(settings.state_file)
    old_file_hash = change_detector.compute_hash(_read_newest(settings))

    # Now data changes but the upload fails.
    result = run_once(
        settings=settings,
        wbs=fake_wbs(_records(workcode, ["WBS-001", "WBS-002"])),
        langflow=fake_langflow(fail=True),
    )

    assert result.changed is True and result.uploaded is False
    assert result.attempts == 1
    assert result.error is not None

    # Newest file untouched (still old content) and temp cleaned up.
    assert change_detector.compute_hash(_read_newest(settings)) == old_file_hash
    assert not settings.temp_file.exists()

    # State hash not advanced; status flagged failed.
    after = state_store.load(settings.state_file)
    assert after.last_hash == before.last_hash
    assert after.last_status == "failed"
    assert after.last_error is not None

    # Changelog recorded the failure (diff shows the attempted change).
    entries = changelog.read(settings.changelog_file)
    assert entries[-1]["status"] == "failed"
    assert entries[-1]["diff"]["added"] == 1


def test_retry_succeeds_on_later_attempt(make_settings, fake_wbs, workcode):
    settings = make_settings(sync_max_retries=3, sync_retry_backoff=0)
    lf = FlakeyLangFlow(fail_times=1)

    result = run_once(settings=settings, wbs=fake_wbs(_records(workcode, ["WBS-001"])), langflow=lf)

    assert result.uploaded is True
    assert result.attempts == 2
    entries = changelog.read(settings.changelog_file)
    assert entries[-1]["status"] == "success"
    assert entries[-1]["attempts"] == 2


def test_retry_exhausts(make_settings, fake_wbs, workcode):
    settings = make_settings(sync_max_retries=3, sync_retry_backoff=0)
    lf = FlakeyLangFlow(fail_times=99)  # always fails

    result = run_once(settings=settings, wbs=fake_wbs(_records(workcode, ["WBS-001"])), langflow=lf)

    assert result.uploaded is False
    assert result.attempts == 3
    entries = changelog.read(settings.changelog_file)
    assert entries[-1]["status"] == "failed"
    assert entries[-1]["attempts"] == 3
