import httpx

from shmoney.sources.sdat import MarylandSDATAdapter


def _mock_client(response_json, capture=None):
    def handler(request):
        if capture is not None:
            capture.append(request)
        return httpx.Response(200, json=response_json)

    return httpx.Client(
        base_url="https://opendata.maryland.gov",
        transport=httpx.MockTransport(handler),
    )


def test_parses_entity_filing():
    client = _mock_client(
        [
            {
                "entity_name": "JOE'S PIZZA LLC",
                "principal_office": "123 Main St, Baltimore, MD 21230",
                "resident_agent_name": "Jane Doe",
                "formation_date": "2019-03-15T00:00:00.000",
            }
        ]
    )
    adapter = MarylandSDATAdapter(
        resource_path="/resource/test.json", client=client
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 1
    b = results[0]
    assert b.source == "md-sdat"
    assert b.name == "JOE'S PIZZA LLC"
    assert "123 Main St" in b.address
    assert b.owner_name == "Jane Doe"
    assert b.registered_at.startswith("2019-03-15")


def test_limit_respected():
    client = _mock_client(
        [
            {"entity_name": f"Biz {i}", "principal_office": f"{i} Main St"}
            for i in range(50)
        ]
    )
    adapter = MarylandSDATAdapter(
        resource_path="/resource/test.json", limit=5, client=client
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 5


def test_missing_optional_fields():
    client = _mock_client(
        [{"entity_name": "Bare Biz", "principal_office": "9 Any St"}]
    )
    adapter = MarylandSDATAdapter(
        resource_path="/resource/test.json", client=client
    )
    b = next(adapter.iter_businesses())
    assert b.name == "Bare Biz"
    assert b.owner_name is None
    assert b.registered_at is None


def test_empty_response():
    client = _mock_client([])
    adapter = MarylandSDATAdapter(
        resource_path="/resource/test.json", client=client
    )
    assert list(adapter.iter_businesses()) == []


def test_sends_limit_query_param():
    captured: list = []
    client = _mock_client([], capture=captured)
    adapter = MarylandSDATAdapter(
        resource_path="/resource/test.json", limit=20, client=client
    )
    list(adapter.iter_businesses())
    assert captured
    assert captured[0].url.params.get("$limit") == "20"
