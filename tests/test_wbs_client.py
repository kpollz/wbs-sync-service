from wbs_sync.wbs_client import WBSClient


def _client(page_size=500, **kw):
    return WBSClient("http://wbs/", "key", page_size=page_size, timeout=5, **kw)


def _responder(pages):
    def responder(request, context):
        context.status_code = 200
        page = int(request.qs["pagenum"][0])
        return pages[page]

    return responder


def test_fetch_all_single_page(requests_mock, workcode):
    requests_mock.get(
        "http://wbs/api/works/search",
        json={"content": [workcode(id="1"), workcode(id="2")], "totalElements": 2},
        complete_qs=False,
    )
    records = _client().fetch_all()
    assert len(records) == 2
    assert records[0].id == "1"


def test_fetch_all_paginates_until_done(requests_mock, workcode):
    pages = {
        1: {"content": [workcode(id="1"), workcode(id="2")], "totalElements": 3},
        2: {"content": [workcode(id="3")], "totalElements": 3},
    }
    requests_mock.get("http://wbs/api/works/search", json=_responder(pages))
    records = _client(page_size=2).fetch_all()
    assert [r.id for r in records] == ["1", "2", "3"]


def test_fetch_all_stops_by_total_elements(requests_mock, workcode):
    # Last page is full-size but totalElements says we're done.
    pages = {
        1: {"content": [workcode(id="1"), workcode(id="2")], "totalElements": 2},
    }
    requests_mock.get("http://wbs/api/works/search", json=_responder(pages))
    records = _client(page_size=2).fetch_all()
    assert len(records) == 2


def test_fetch_all_skips_malformed_records(requests_mock, workcode):
    # workCategory as an int cannot coerce into the nested model -> skipped.
    bad = workcode(id="bad", workCategory=123)
    pages = {1: {"content": [workcode(id="1"), bad], "totalElements": 1}}
    requests_mock.get("http://wbs/api/works/search", json=_responder(pages))
    records = _client(page_size=10).fetch_all()
    assert [r.id for r in records] == ["1"]


def test_fetch_all_no_total_uses_page_size(requests_mock, workcode):
    # No totalElements: stop only when a page returns fewer than page_size.
    pages = {
        1: {"content": [workcode(id="1"), workcode(id="2")]},
        2: {"content": [workcode(id="3")]},
    }
    requests_mock.get("http://wbs/api/works/search", json=_responder(pages))
    records = _client(page_size=2).fetch_all()
    assert len(records) == 3
