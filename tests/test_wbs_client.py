from wbs_sync.wbs_client import WBSClient


def _client(**kw):
    page_size = kw.pop("page_size", 500)
    return WBSClient("http://wbs/", "key", page_size=page_size, timeout=5, **kw)


def _responder(pages):
    def responder(request, context):
        context.status_code = 200
        page = int(request.qs["pagenum"][0])
        return pages[page]

    return responder


def test_fetch_departments(requests_mock):
    requests_mock.get(
        "http://wbs/api/departments",
        json={
            "resultCode": 0,
            "message": "ok",
            "value": [
                {"id": 1, "code": "S", "name": "Sales", "createdDate": "x"},
                {"id": 2, "code": "I", "name": "IT & Ops"},
            ],
        },
    )
    depts = _client().fetch_departments()
    assert [d.name for d in depts] == ["Sales", "IT & Ops"]
    assert depts[0].id == 1


def test_fetch_departments_handles_bare_list(requests_mock):
    requests_mock.get("http://wbs/api/departments", json=[{"id": 1, "name": "Sales"}])
    depts = _client().fetch_departments()
    assert len(depts) == 1 and depts[0].name == "Sales"


def test_fetch_works_paginates(requests_mock, workcode):
    pages = {
        1: {"content": [workcode(id="1"), workcode(id="2")], "totalElements": 3},
        2: {"content": [workcode(id="3")], "totalElements": 3},
    }
    requests_mock.get("http://wbs/api/works/search", json=_responder(pages))
    records = _client(page_size=2).fetch_works()
    assert [r.id for r in records] == ["1", "2", "3"]


def test_fetch_work_profiles_passes_department_name(requests_mock, workcode):
    requests_mock.get(
        "http://wbs/api/work-profiles",
        json={"content": [workcode(id="1")], "totalElements": 1},
    )
    records = _client(page_size=10).fetch_work_profiles("Sales & Ops")
    assert len(records) == 1
    # departmentName uses the ORIGINAL name (case preserved); requests URL-encodes it.
    # (requests_mock's .qs accessor lowercases values, so check the raw URL instead.)
    assert "departmentName=Sales" in requests_mock.last_request.url


def test_fetch_work_profiles_paginates(requests_mock, workcode):
    pages = {
        1: {"content": [workcode(id="1"), workcode(id="2")], "totalElements": 3},
        2: {"content": [workcode(id="3")], "totalElements": 3},
    }
    requests_mock.get("http://wbs/api/work-profiles", json=_responder(pages))
    records = _client(page_size=2).fetch_work_profiles("Sales")
    assert [r.id for r in records] == ["1", "2", "3"]
