import httpx

from shmoney.sources.yelp import YelpFusionAdapter


def _mock_client(response_json):
    def handler(request):
        assert request.url.path.endswith("/businesses/search")
        assert request.headers["Authorization"] == "Bearer testkey"
        return httpx.Response(200, json=response_json)

    return httpx.Client(
        base_url="https://api.yelp.com/v3",
        headers={"Authorization": "Bearer testkey"},
        transport=httpx.MockTransport(handler),
    )


def test_parses_single_business():
    client = _mock_client(
        {
            "businesses": [
                {
                    "name": "Joe's Pizza",
                    "display_phone": "(410) 555-1234",
                    "url": "https://www.yelp.com/biz/joes-pizza",
                    "location": {
                        "display_address": ["123 Main St", "Baltimore, MD 21230"],
                        "neighborhoods": ["Federal Hill"],
                    },
                    "categories": [{"title": "Pizza"}, {"title": "Italian"}],
                    "review_count": 42,
                }
            ]
        }
    )
    adapter = YelpFusionAdapter("testkey", client=client)
    results = list(adapter.iter_businesses())
    assert len(results) == 1
    b = results[0]
    assert b.source == "yelp"
    assert b.name == "Joe's Pizza"
    assert b.phone == "(410) 555-1234"
    assert "123 Main St" in b.address
    assert b.neighborhood == "Federal Hill"
    assert "Pizza" in b.business_type
    assert b.review_count == 42
    assert b.website is None  # Fusion url is Yelp page, not real website
    assert "yelp.com/biz/joes-pizza" in (b.yelp_or_google_listing or "")


def test_empty_businesses_list():
    client = _mock_client({"businesses": []})
    adapter = YelpFusionAdapter("testkey", client=client)
    assert list(adapter.iter_businesses()) == []


def test_missing_optional_fields():
    client = _mock_client(
        {
            "businesses": [
                {
                    "name": "Bare Biz",
                    "location": {"display_address": ["9 Any St"]},
                    "categories": [],
                }
            ]
        }
    )
    adapter = YelpFusionAdapter("testkey", client=client)
    b = next(adapter.iter_businesses())
    assert b.name == "Bare Biz"
    assert b.phone is None
    assert b.review_count is None
    assert b.neighborhood is None
