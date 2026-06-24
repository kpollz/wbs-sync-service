from wbs_sync.models import WorkCode
from wbs_sync.transformer import to_slim, to_slim_list


def test_to_slim_keeps_required_fields(workcode):
    slim = to_slim(WorkCode(**workcode()))
    assert slim.name == "Design module"
    assert slim.code == "WBS-001"
    assert slim.description == "Thiết kế module"
    assert slim.input == "yêu cầu"
    assert slim.output == "bản vẽ"
    assert slim.task == "vẽ"


def test_to_slim_flattens_named_refs_to_string(workcode):
    slim = to_slim(WorkCode(**workcode()))
    assert slim.workCategory == "Engineering"  # not {id, name}
    assert slim.job == "Designer"
    assert isinstance(slim.workCategory, str)


def test_to_slim_drops_metadata(workcode):
    slim = to_slim(WorkCode(**workcode()))
    assert not hasattr(slim, "id")
    assert not hasattr(slim, "createdBy")
    assert not hasattr(slim, "updatedAt")


def test_to_slim_missing_fields_become_none():
    slim = to_slim(WorkCode(name="only-name"))
    assert slim.name == "only-name"
    assert slim.code is None
    assert slim.workCategory is None
    assert slim.job is None


def test_to_slim_list_maps_all(workcode):
    records = [WorkCode(**workcode(id="1")), WorkCode(**workcode(id="2"))]
    slim = to_slim_list(records)
    assert len(slim) == 2


def test_workcode_accepts_integer_ids(workcode):
    """The WBS API returns ids as ints; they must parse without error."""
    record = WorkCode(
        **workcode(
            id=123,
            workCategory={"id": 456, "name": "Engineering"},
            job={"id": 789, "name": "Designer"},
        )
    )
    slim = to_slim(record)
    assert record.id == 123
    assert record.workCategory.id == 456
    # slim flattens to names and drops ids entirely
    assert slim.workCategory == "Engineering"
    assert slim.job == "Designer"
    assert not hasattr(slim, "id")
