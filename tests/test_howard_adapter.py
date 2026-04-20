import httpx

from shmoney.sources.howard import HowardLicenseAdapter


BASE_URL = "https://opendata.howardcountymd.gov"
RESOURCE_PATH = "/resource/tk3t-mn7e.json"


def _client(handler):
    return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _sample(**overrides):
    base = {
        "license_class": "B",
        "description": "Beer, Wine and Liquor",
        "corp_name": "Ruma, Inc.",
        "trade_name": "Curry & Kabob",
        "address": "10451 Twin Rivers Road, #132, Columbia MD 21044",
    }
    base.update(overrides)
    return base


def _once(records):
    def handler(request):
        assert request.url.path == RESOURCE_PATH
        offset = int(request.url.params.get("$offset", "0"))
        return httpx.Response(200, json=records if offset == 0 else [])
    return handler


def test_parses_record_into_raw_business():
    def handler(request):
        assert request.url.path == RESOURCE_PATH
        offset = int(request.url.params.get("$offset", "0"))
        return httpx.Response(200, json=[_sample()] if offset == 0 else [])

    adapter = HowardLicenseAdapter(client=_client(handler), min_interval_s=0.0)
    results = list(adapter.iter_businesses())

    assert len(results) == 1
    b = results[0]
    assert b.source == "howard-license"
    assert b.name == "Curry & Kabob"
    # owner_name is intentionally None — Howard's Socrata feed has no human
    # owner, only the licensed entity. A later canonical_key join with
    # OpenCorporates/MBE fills it in with a real person.
    assert b.owner_name is None
    assert b.address == "10451 Twin Rivers Road, #132, Columbia MD 21044"
    assert b.business_type == "Beer, Wine and Liquor"


def test_falls_back_to_corp_name_when_no_trade_name():
    adapter = HowardLicenseAdapter(
        client=_client(_once([_sample(trade_name="", corp_name="Solo LLC")])),
        min_interval_s=0.0,
    )
    b = next(adapter.iter_businesses())
    assert b.name == "Solo LLC"


def test_paginates_via_offset_until_empty_page():
    pages: list[int] = []

    def handler(request):
        offset = int(request.url.params.get("$offset", "0"))
        pages.append(offset)
        if offset == 0:
            return httpx.Response(200, json=[_sample(trade_name=f"A{i}") for i in range(2)])
        if offset == 2:
            return httpx.Response(200, json=[_sample(trade_name="B0")])
        return httpx.Response(200, json=[])

    adapter = HowardLicenseAdapter(
        client=_client(handler), page_size=2, min_interval_s=0.0
    )
    results = list(adapter.iter_businesses())
    assert [b.name for b in results] == ["A0", "A1", "B0"]
    assert pages == [0, 2]


def test_limit_respected():
    adapter = HowardLicenseAdapter(
        client=_client(_once([_sample(trade_name=f"Biz {i}") for i in range(50)])),
        limit=3,
        page_size=50,
        min_interval_s=0.0,
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 3


def test_rate_limit_sleeps_between_pages():
    def handler(request):
        offset = int(request.url.params.get("$offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=[_sample()])
        return httpx.Response(200, json=[])

    clock = FakeClock()
    adapter = HowardLicenseAdapter(
        client=_client(handler),
        page_size=1,
        min_interval_s=0.5,
        now=clock.now,
        sleep=clock.sleep,
    )
    list(adapter.iter_businesses())
    assert clock.sleeps == [0.5]


def test_sends_limit_and_offset_params():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[])

    adapter = HowardLicenseAdapter(
        client=_client(handler), page_size=500, min_interval_s=0.0
    )
    list(adapter.iter_businesses())
    req = captured[0]
    assert req.url.params["$limit"] == "500"
    assert req.url.params["$offset"] == "0"


def test_sends_app_token_when_provided():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[])

    adapter = HowardLicenseAdapter(
        client=_client(handler), app_token="tok123", min_interval_s=0.0
    )
    list(adapter.iter_businesses())
    assert captured[0].headers.get("X-App-Token") == "tok123"
