import httpx

from shmoney.sources.opencorporates import OpenCorporatesMDAdapter


def _client(handler):
    return httpx.Client(
        base_url="https://api.opencorporates.com",
        transport=httpx.MockTransport(handler),
    )


def _search_response(companies, total_pages=1):
    return {"results": {"companies": companies, "total_pages": total_pages}}


def _detail_response(officers):
    return {"results": {"company": {"officers": officers}}}


def test_parses_company_with_officer():
    def handler(request):
        if "/companies/search" in request.url.path:
            return httpx.Response(200, json=_search_response([
                {"company": {
                    "name": "JOE'S PIZZA LLC",
                    "company_number": "W12345678",
                    "jurisdiction_code": "us_md",
                    "registered_address_in_full": "123 Main St, Baltimore, MD 21230",
                    "incorporation_date": "2019-03-15",
                }}
            ]))
        if request.url.path.endswith("/companies/us_md/W12345678"):
            return httpx.Response(200, json=_detail_response([
                {"officer": {"name": "Jane Doe", "position": "member"}}
            ]))
        return httpx.Response(404)

    adapter = OpenCorporatesMDAdapter(
        api_token="t", query="pizza", client=_client(handler), throttle_seconds=0
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 1
    b = results[0]
    assert b.source == "md-sdat"
    assert b.name == "JOE'S PIZZA LLC"
    assert "123 Main St" in b.address
    assert b.owner_name == "Jane Doe"
    assert b.registered_at == "2019-03-15"


def test_skips_inactive_officers():
    def handler(request):
        if "/companies/search" in request.url.path:
            return httpx.Response(200, json=_search_response([
                {"company": {"name": "Biz", "company_number": "1", "jurisdiction_code": "us_md"}}
            ]))
        return httpx.Response(200, json=_detail_response([
            {"officer": {"name": "Old Boss", "inactive": True}},
            {"officer": {"name": "New Boss", "inactive": False}},
        ]))

    adapter = OpenCorporatesMDAdapter(
        api_token="t", client=_client(handler), throttle_seconds=0
    )
    b = next(adapter.iter_businesses())
    assert b.owner_name == "New Boss"


def test_officer_fetch_disabled():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "/companies/search" in request.url.path:
            return httpx.Response(200, json=_search_response([
                {"company": {"name": "Biz", "company_number": "1", "jurisdiction_code": "us_md"}}
            ]))
        return httpx.Response(500)

    adapter = OpenCorporatesMDAdapter(
        api_token="t", fetch_officers=False, client=_client(handler), throttle_seconds=0
    )
    b = next(adapter.iter_businesses())
    assert b.owner_name is None
    assert all("/companies/us_md/" not in p for p in calls)


def test_limit_respected_across_pages():
    def handler(request):
        if "/companies/search" in request.url.path:
            page = int(request.url.params.get("page", "1"))
            companies = [
                {"company": {"name": f"Biz {page}-{i}", "company_number": f"{page}{i}", "jurisdiction_code": "us_md"}}
                for i in range(100)
            ]
            return httpx.Response(200, json=_search_response(companies, total_pages=10))
        return httpx.Response(200, json=_detail_response([]))

    adapter = OpenCorporatesMDAdapter(
        api_token="t", limit=5, fetch_officers=False, client=_client(handler), throttle_seconds=0
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 5


def test_empty_results():
    def handler(request):
        return httpx.Response(200, json=_search_response([]))

    adapter = OpenCorporatesMDAdapter(
        api_token="t", client=_client(handler), throttle_seconds=0
    )
    assert list(adapter.iter_businesses()) == []


def test_missing_officer_detail_returns_none():
    def handler(request):
        if "/companies/search" in request.url.path:
            return httpx.Response(200, json=_search_response([
                {"company": {"name": "Biz", "company_number": "X", "jurisdiction_code": "us_md"}}
            ]))
        return httpx.Response(404)

    adapter = OpenCorporatesMDAdapter(
        api_token="t", client=_client(handler), throttle_seconds=0
    )
    b = next(adapter.iter_businesses())
    assert b.owner_name is None
    assert b.name == "Biz"


def test_sends_jurisdiction_and_token():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_search_response([]))

    adapter = OpenCorporatesMDAdapter(
        api_token="tok123",
        jurisdiction_code="us_md",
        query="coffee",
        client=_client(handler),
        throttle_seconds=0,
    )
    list(adapter.iter_businesses())
    req = captured[0]
    assert req.url.params["jurisdiction_code"] == "us_md"
    assert req.url.params["api_token"] == "tok123"
    assert req.url.params["q"] == "coffee"
