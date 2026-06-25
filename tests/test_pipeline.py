from pathlib import Path

from wbs_sync import changelog, state as state_store
from wbs_sync.models import Department, WorkCode
from wbs_sync.pipeline import run_once

BASE = "wbs_agent_knowledge"


def _records(workcode, codes):
    return [WorkCode(**workcode(code=c)) for c in codes]


def _dept(name):
    return Department(id=name, code=name, name=name, createdDate="x")


def _cl(settings, subdir=""):
    """Read a target's changelog (root for default, <slug>/ for a part)."""
    return changelog.read(Path(settings.state_dir) / subdir / "changelog.jsonl")


def test_first_run_pushes_default_and_parts(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    wbs = fake_wbs(
        departments=[_dept("Sales"), _dept("IT")],
        works=_records(workcode, ["D-1", "D-2"]),
        profiles={"Sales": _records(workcode, ["S-1"]), "IT": _records(workcode, ["I-1"])},
    )
    lf = fake_langflow()

    res = run_once(settings=settings, wbs=wbs, langflow=lf)

    assert res.targets == 3
    assert res.changed == 3 and res.uploaded == 3 and res.failed == 0
    assert sorted(lf.replace_calls) == sorted(
        [f"{BASE}.json", f"{BASE}_it.json", f"{BASE}_sales.json"]
    )

    # data files at the new layout: default at root, each part in its own folder
    root = Path(settings.state_dir)
    assert (root / f"{BASE}.json").exists()
    assert (root / "it" / f"{BASE}_it.json").exists()
    assert (root / "sales" / f"{BASE}_sales.json").exists()

    # state has 3 targets
    state = state_store.load(settings.state_file)
    assert set(state.targets) == {"default", "part:it", "part:sales"}

    # changelogs: default in root, each part in its own folder
    assert {e["target"] for e in _cl(settings)} == {"default"}
    assert {e["target"] for e in _cl(settings, "it")} == {"part:it"}
    assert {e["target"] for e in _cl(settings, "sales")} == {"part:sales"}


def test_unchanged_skips_all_targets(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    build = lambda: fake_wbs(  # noqa: E731
        departments=[_dept("Sales")],
        works=_records(workcode, ["D-1"]),
        profiles={"Sales": _records(workcode, ["S-1"])},
    )
    lf = fake_langflow()
    run_once(settings=settings, wbs=build(), langflow=lf)
    calls_after_first = len(lf.replace_calls)

    res = run_once(settings=settings, wbs=build(), langflow=lf)
    assert res.changed == 0
    assert len(lf.replace_calls) == calls_after_first  # no new uploads


def test_per_part_change_only_repushes_that_part(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    lf = fake_langflow()
    run_once(
        settings=settings,
        wbs=fake_wbs(
            departments=[_dept("Sales"), _dept("IT")],
            works=_records(workcode, ["D-1"]),
            profiles={"Sales": _records(workcode, ["S-1"]), "IT": _records(workcode, ["I-1"])},
        ),
        langflow=lf,
    )
    calls_after_first = len(lf.replace_calls)

    # Change ONLY Sales (add a record); default and IT unchanged.
    res = run_once(
        settings=settings,
        wbs=fake_wbs(
            departments=[_dept("Sales"), _dept("IT")],
            works=_records(workcode, ["D-1"]),
            profiles={"Sales": _records(workcode, ["S-1", "S-2"]), "IT": _records(workcode, ["I-1"])},
        ),
        langflow=lf,
    )
    assert res.changed == 1
    assert lf.replace_calls[calls_after_first:] == [f"{BASE}_sales.json"]
    # sales changelog got a 2nd entry; it changelog still has 1
    assert len(_cl(settings, "sales")) == 2
    assert len(_cl(settings, "it")) == 1


def test_orphan_cleanup_removes_deleted_part(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    lf = fake_langflow()
    sales = {"Sales": _records(workcode, ["S-1"])}
    run_once(
        settings=settings,
        wbs=fake_wbs(
            departments=[_dept("Sales"), _dept("IT")],
            works=_records(workcode, ["D-1"]),
            profiles={**sales, "IT": _records(workcode, ["I-1"])},
        ),
        langflow=lf,
    )

    # IT disappears from the department list.
    res = run_once(
        settings=settings,
        wbs=fake_wbs(departments=[_dept("Sales")], works=_records(workcode, ["D-1"]), profiles=sales),
        langflow=lf,
    )
    assert res.removed == 1
    assert f"{BASE}_it" in lf.deleted_bases
    state = state_store.load(settings.state_file)
    assert "part:it" not in state.targets
    assert "part:sales" in state.targets

    # removal recorded in the ROOT changelog; the part's folder is deleted
    removed = [e for e in _cl(settings) if e["status"] == "removed"]
    assert len(removed) == 1 and removed[0]["target"] == "part:it"
    assert not (Path(settings.state_dir) / "it").exists()


def test_orphan_guard_skips_cleanup_when_departments_empty(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    lf = fake_langflow()
    run_once(
        settings=settings,
        wbs=fake_wbs(
            departments=[_dept("Sales")],
            works=_records(workcode, ["D-1"]),
            profiles={"Sales": _records(workcode, ["S-1"])},
        ),
        langflow=lf,
    )

    # Glitch: empty department list — must NOT delete Sales.
    res = run_once(
        settings=settings,
        wbs=fake_wbs(departments=[], works=_records(workcode, ["D-1"]), profiles={}),
        langflow=lf,
    )
    assert res.removed == 0
    assert lf.deleted_bases == []
    state = state_store.load(settings.state_file)
    assert "part:sales" in state.targets  # preserved
    assert (Path(settings.state_dir) / "sales").exists()  # folder not wiped


def test_force_pushes_all_targets(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings()
    lf = fake_langflow()
    build = lambda: fake_wbs(  # noqa: E731
        departments=[_dept("Sales")],
        works=_records(workcode, ["D-1"]),
        profiles={"Sales": _records(workcode, ["S-1"])},
    )
    run_once(settings=settings, wbs=build(), langflow=lf)
    n1 = len(lf.replace_calls)

    res = run_once(force=True, settings=settings, wbs=build(), langflow=lf)
    assert res.changed == 2  # default + sales re-pushed
    assert len(lf.replace_calls) == n1 + 2


def test_failure_marks_targets_failed_but_advances_newest(
    make_settings, fake_wbs, fake_langflow, workcode
):
    settings = make_settings(sync_max_retries=1, sync_retry_backoff=0)
    lf = fake_langflow(fail=True)
    res = run_once(
        settings=settings,
        wbs=fake_wbs(
            departments=[_dept("Sales")],
            works=_records(workcode, ["D-1"]),
            profiles={"Sales": _records(workcode, ["S-1"])},
        ),
        langflow=lf,
    )
    assert res.failed == 2 and res.uploaded == 0
    state = state_store.load(settings.state_file)
    assert all(ts.last_status == "failed" for ts in state.targets.values())
    # newest files still advanced (promote-first design), at the new layout
    root = Path(settings.state_dir)
    assert (root / f"{BASE}.json").exists()
    assert (root / "sales" / f"{BASE}_sales.json").exists()


def test_default_disabled_pushes_only_parts(make_settings, fake_wbs, fake_langflow, workcode):
    settings = make_settings(sync_default_enabled=False)
    lf = fake_langflow()
    res = run_once(
        settings=settings,
        wbs=fake_wbs(
            departments=[_dept("Sales")],
            works=_records(workcode, ["D-1"]),
            profiles={"Sales": _records(workcode, ["S-1"])},
        ),
        langflow=lf,
    )
    assert res.targets == 1
    assert lf.replace_calls == [f"{BASE}_sales.json"]
