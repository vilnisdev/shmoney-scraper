import httpx

from shmoney.sources.baltimore_city import BaltimoreCityLicenseAdapter


BASE_URL = "https://services1.arcgis.com"
QUERY_PATH = "/UWYHeuuJISiGmgXx/arcgis/rest/services/MBWOO_Geocoded/FeatureServer/0/query"


def _client(handler):
    return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))


def _feature(**attrs):
    return {"attributes": attrs}


def _query_response(features, exceeded=False):
    return {"features": features, "exceededTransferLimit": exceeded}


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _sample_attrs(**overrides):
    base = {
        "user_company": "Noble's Landscape Service",
        "user_firstname": "Jay",
        "user_lastname": "Noble",
        "user_streetaddress": "3314 Elgin Avenue",
        "user_city": "Baltimore",
        "user_state": "Md",
        "user_zipcode": "21216-",
        "user_phone": "(410)233-4915",
        "user_website": "NULL",
        "user_category": "SERVICES",
        "user_origcert": "2000-01-16T00:00:00",
        "user_contractstatus": "CERTIFY",
        "user_contracttype": "MBE",
    }
    base.update(overrides)
    return base


def test_parses_feature_into_raw_business():
    def handler(request):
        assert request.url.path == QUERY_PATH
        return httpx.Response(200, json=_query_response([_feature(**_sample_attrs())]))

    adapter = BaltimoreCityLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    results = list(adapter.iter_businesses())

    assert len(results) == 1
    b = results[0]
    assert b.source == "baltimore-city-license"
    assert b.name == "Noble's Landscape Service"
    assert b.owner_name == "Jay Noble"
    assert "3314 Elgin Avenue" in b.address
    assert "Baltimore" in b.address
    assert "21216" in b.address
    assert b.phone == "(410)233-4915"
    assert b.registered_at == "2000-01-16T00:00:00"
    assert b.business_type == "SERVICES"


def test_null_website_treated_as_none():
    def handler(request):
        return httpx.Response(200, json=_query_response([
            _feature(**_sample_attrs(user_website="NULL"))
        ]))

    adapter = BaltimoreCityLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    b = next(adapter.iter_businesses())
    assert b.website is None


def test_real_website_preserved():
    def handler(request):
        return httpx.Response(200, json=_query_response([
            _feature(**_sample_attrs(user_website="https://example.com"))
        ]))

    adapter = BaltimoreCityLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    b = next(adapter.iter_businesses())
    assert b.website == "https://example.com"


def test_skips_inactive_certifications():
    def handler(request):
        return httpx.Response(200, json=_query_response([
            _feature(**_sample_attrs(user_company="Active Co", user_contractstatus="CERTIFY")),
            _feature(**_sample_attrs(user_company="Closed Co", user_contractstatus="CLOSED")),
            _feature(**_sample_attrs(user_company="Expired Co", user_contractstatus="EXPIRED")),
        ]))

    adapter = BaltimoreCityLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    names = [b.name for b in adapter.iter_businesses()]
    assert names == ["Active Co"]


def test_paginates_until_no_more_records():
    pages: list[int] = []

    def handler(request):
        offset = int(request.url.params.get("resultOffset", "0"))
        pages.append(offset)
        if offset == 0:
            feats = [_feature(**_sample_attrs(user_company=f"Biz A{i}")) for i in range(2)]
            return httpx.Response(200, json=_query_response(feats, exceeded=True))
        if offset == 2:
            feats = [_feature(**_sample_attrs(user_company=f"Biz B{i}")) for i in range(1)]
            return httpx.Response(200, json=_query_response(feats, exceeded=False))
        return httpx.Response(200, json=_query_response([]))

    adapter = BaltimoreCityLicenseAdapter(
        client=_client(handler), page_size=2, min_interval_s=0.0
    )
    results = list(adapter.iter_businesses())
    assert [b.name for b in results] == ["Biz A0", "Biz A1", "Biz B0"]
    assert pages == [0, 2]


def test_limit_respected():
    def handler(request):
        feats = [_feature(**_sample_attrs(user_company=f"Biz {i}")) for i in range(50)]
        return httpx.Response(200, json=_query_response(feats, exceeded=True))

    adapter = BaltimoreCityLicenseAdapter(
        client=_client(handler), limit=3, page_size=50, min_interval_s=0.0
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 3


def test_rate_limit_sleeps_between_pages():
    def handler(request):
        offset = int(request.url.params.get("resultOffset", "0"))
        if offset == 0:
            return httpx.Response(200, json=_query_response(
                [_feature(**_sample_attrs())], exceeded=True
            ))
        return httpx.Response(200, json=_query_response([]))

    clock = FakeClock()
    adapter = BaltimoreCityLicenseAdapter(
        client=_client(handler),
        page_size=1,
        min_interval_s=0.5,
        now=clock.now,
        sleep=clock.sleep,
    )
    list(adapter.iter_businesses())
    assert clock.sleeps == [0.5]


def test_sends_expected_query_params():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_query_response([]))

    adapter = BaltimoreCityLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    list(adapter.iter_businesses())
    req = captured[0]
    assert req.url.params["where"] == "1=1"
    assert req.url.params["outFields"] == "*"
    assert req.url.params["f"] == "json"
