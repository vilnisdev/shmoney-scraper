import httpx

from shmoney.sources.anne_arundel import AnneArundelLicenseAdapter


BASE_URL = "https://gis.aacounty.org"
QUERY_PATH = (
    "/arcgis/rest/services/OpenData/Planning_aacoPZProd_OpenData/FeatureServer/9/query"
)


def _client(handler):
    return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))


def _feature(**attrs):
    return {"attributes": attrs}


def _query_response(features, exceeded=False):
    return {"features": features, "exceededTransferLimit": exceeded}


def _sample(**overrides):
    base = {
        "user_trade_name": "Goldberg's Liquors",
        "user_street_address": "5106 Ritchie Highway",
        "user_city": "Baltimore",
        "user_st": "MD",
        "user_zip": "21225-3051",
        "class": "A: Package Goods",
    }
    base.update(overrides)
    return base


def test_parses_feature_into_raw_business():
    def handler(request):
        assert request.url.path == QUERY_PATH
        return httpx.Response(200, json=_query_response([_feature(**_sample())]))

    adapter = AnneArundelLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    results = list(adapter.iter_businesses())

    assert len(results) == 1
    b = results[0]
    assert b.source == "anne-arundel-license"
    assert b.name == "Goldberg's Liquors"
    assert "5106 Ritchie Highway" in b.address
    assert "Baltimore" in b.address
    assert "21225" in b.address
    assert b.business_type == "A: Package Goods"
    # No human owner in the AA feed — same stance as Howard.
    assert b.owner_name is None


def test_skips_records_without_trade_name():
    def handler(request):
        return httpx.Response(200, json=_query_response([
            _feature(**_sample(user_trade_name="")),
            _feature(**_sample(user_trade_name="Real Biz")),
        ]))

    adapter = AnneArundelLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    names = [b.name for b in adapter.iter_businesses()]
    assert names == ["Real Biz"]


def test_paginates_until_no_more_records():
    pages: list[int] = []

    def handler(request):
        offset = int(request.url.params.get("resultOffset", "0"))
        pages.append(offset)
        if offset == 0:
            feats = [_feature(**_sample(user_trade_name=f"Biz A{i}")) for i in range(2)]
            return httpx.Response(200, json=_query_response(feats, exceeded=True))
        return httpx.Response(200, json=_query_response([]))

    adapter = AnneArundelLicenseAdapter(
        client=_client(handler), page_size=2, min_interval_s=0.0
    )
    results = list(adapter.iter_businesses())
    assert [b.name for b in results] == ["Biz A0", "Biz A1"]
    assert pages == [0, 2]


def test_limit_respected():
    def handler(request):
        feats = [_feature(**_sample(user_trade_name=f"Biz {i}")) for i in range(50)]
        return httpx.Response(200, json=_query_response(feats, exceeded=True))

    adapter = AnneArundelLicenseAdapter(
        client=_client(handler), limit=3, page_size=50, min_interval_s=0.0
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 3


def test_sends_expected_query_params():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_query_response([]))

    adapter = AnneArundelLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    list(adapter.iter_businesses())
    req = captured[0]
    assert req.url.params["where"] == "1=1"
    assert req.url.params["outFields"] == "*"
    assert req.url.params["f"] == "json"
