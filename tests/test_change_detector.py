from wbs_sync import change_detector, transformer
from wbs_sync.models import WorkCode


def _slim(**over) -> dict:
    from tests.conftest import make_workcode

    return transformer.to_slim(WorkCode(**make_workcode(**over))).model_dump()


# --- hashing / change detection ---


def test_hash_is_deterministic():
    items = [_slim()]
    assert change_detector.compute_hash(items) == change_detector.compute_hash(list(items))


def test_hash_independent_of_input_order():
    a, b, c = _slim(code="A"), _slim(code="B"), _slim(code="C")
    assert change_detector.compute_hash([a, b, c]) == change_detector.compute_hash([c, a, b])


def test_hash_changes_on_field_change():
    h1 = change_detector.compute_hash([_slim(description="v1")])
    h2 = change_detector.compute_hash([_slim(description="v2")])
    assert h1 != h2


def test_hash_changes_on_count():
    assert change_detector.compute_hash([_slim()]) != change_detector.compute_hash(
        [_slim(), _slim(code="X")]
    )


def test_hash_stable_with_unicode():
    h1 = change_detector.compute_hash([_slim(description="Thiết kế module")])
    h2 = change_detector.compute_hash([_slim(description="Thiết kế module")])
    assert h1 == h2


def test_has_changed_first_run():
    assert change_detector.has_changed("any", None) is True


def test_has_changed_same_is_false():
    assert change_detector.has_changed("h", "h") is False


def test_has_changed_different_is_true():
    assert change_detector.has_changed("h1", "h2") is True


# --- diff ---


def test_diff_all_new():
    d = change_detector.compute_diff([], [_slim(code="A"), _slim(code="B"), _slim(code="C")])
    assert d["added"] == 3
    assert d["removed"] == 0
    assert d["updated"] == 0
    assert d["unchanged"] == 0
    assert d["total_new"] == 3
    assert set(d["sample_added"]) == {"A", "B", "C"}


def test_diff_all_unchanged():
    items = [_slim(code="A"), _slim(code="B")]
    d = change_detector.compute_diff(items, [dict(x) for x in items])
    assert d["added"] == 0 and d["removed"] == 0 and d["updated"] == 0
    assert d["unchanged"] == 2


def test_diff_detects_updated_field():
    old = [_slim(code="A", description="v1")]
    new = [_slim(code="A", description="v2")]
    d = change_detector.compute_diff(old, new)
    assert d["updated"] == 1
    assert d["unchanged"] == 0
    assert d["sample_updated"][0]["fields"] == ["description"]


def test_diff_detects_removed():
    old = [_slim(code="A"), _slim(code="B")]
    new = [_slim(code="A")]
    d = change_detector.compute_diff(old, new)
    assert d["removed"] == 1
    assert d["sample_removed"] == ["B"]


def test_diff_sample_is_capped():
    new = [_slim(code=f"C{i}") for i in range(10)]
    d = change_detector.compute_diff([], new)
    assert len(d["sample_added"]) == 5
    assert d["added"] == 10
