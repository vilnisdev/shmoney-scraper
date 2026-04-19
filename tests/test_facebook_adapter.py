import httpx
import pytest

from shmoney.sources.facebook import FacebookPagesAdapter, FacebookParseError


ACTIVE_PAGE_HTML = """
<!doctype html>
<html>
<head>
<meta property="og:title" content="Joe's Pizza Baltimore" />
<meta property="og:type" content="business.business" />
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "Joe's Pizza Baltimore",
  "telephone": "(410) 555-0100",
  "url": "https://joespizzabmore.com",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "123 Light St",
    "addressLocality": "Baltimore",
    "addressRegion": "MD",
    "postalCode": "21230"
  },
  "priceRange": "$$"
}
</script>
<meta property="og:description" content="Pizza & Italian" />
</head>
<body>Page body</body>
</html>
"""

FB_ONLY_PAGE_HTML = """
<!doctype html>
<html><head>
<meta property="og:title" content="Crab Shack" />
<script type="application/ld+json">
{"@type":"LocalBusiness","name":"Crab Shack",
 "telephone":"410-555-0200",
 "address":{"streetAddress":"9 Pier Rd","addressLocality":"Baltimore","addressRegion":"MD"}}
</script>
</head><body/></html>
"""

TAKEDOWN_HTML = "<html><body>This content isn't available right now</body></html>"


def _transport_map(mapping: dict[str, httpx.Response]):
    """Map URL -> Response. Unknown URLs 500."""

    def handler(request):
        url = str(request.url)
        if url in mapping:
            return mapping[url]
        return httpx.Response(500, text=f"unexpected {url}")

    return httpx.MockTransport(handler)


def _client(mapping):
    return httpx.Client(transport=_transport_map(mapping), headers={"User-Agent": "test"})


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _allow_all(url: str) -> bool:
    return True


def _deny_all(url: str) -> bool:
    return False


def test_parses_active_page_with_external_website():
    url = "https://www.facebook.com/joespizzabmore"
    client = _client({url: httpx.Response(200, text=ACTIVE_PAGE_HTML)})
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="shmoney-scraper (contact: ops@example.com)",
        page_urls=[url],
        client=client,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_allow_all,
    )
    results = list(adapter.iter_businesses())
    assert len(results) == 1
    b = results[0]
    assert b.source == "facebook"
    assert b.name == "Joe's Pizza Baltimore"
    assert "123 Light St" in b.address
    assert "Baltimore" in b.address
    assert b.phone == "(410) 555-0100"
    assert b.website == "https://joespizzabmore.com"
    assert b.yelp_or_google_listing == url


def test_facebook_only_page_sets_website_to_fb_url():
    url = "https://www.facebook.com/crabshack"
    client = _client({url: httpx.Response(200, text=FB_ONLY_PAGE_HTML)})
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="ua",
        page_urls=[url],
        client=client,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_allow_all,
    )
    b = next(adapter.iter_businesses())
    assert b.website == url  # so classifier tags social-only (facebook)


def test_skips_404_takedown():
    url = "https://www.facebook.com/gone"
    client = _client({url: httpx.Response(404, text=TAKEDOWN_HTML)})
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="ua",
        page_urls=[url],
        client=client,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_allow_all,
    )
    assert list(adapter.iter_businesses()) == []


def test_robots_disallow_skips_host_without_fetch(caplog):
    url = "https://www.facebook.com/somebiz"
    fetched: list[str] = []

    def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, text=ACTIVE_PAGE_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="ua",
        page_urls=[url],
        client=client,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_deny_all,
    )
    with caplog.at_level("INFO"):
        assert list(adapter.iter_businesses()) == []
    assert fetched == []
    assert any("robots" in r.message.lower() for r in caplog.records)


def test_rate_limit_sleeps_between_same_host_fetches():
    url1 = "https://www.facebook.com/a"
    url2 = "https://www.facebook.com/b"
    client = _client(
        {
            url1: httpx.Response(200, text=ACTIVE_PAGE_HTML),
            url2: httpx.Response(200, text=ACTIVE_PAGE_HTML),
        }
    )
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="ua",
        page_urls=[url1, url2],
        client=client,
        min_interval_s=3.0,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_allow_all,
    )
    list(adapter.iter_businesses())
    # first fetch: no sleep; second fetch on same host: sleep ~3s
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == pytest.approx(3.0, abs=0.01)


def test_parse_fails_loudly_when_name_missing():
    url = "https://www.facebook.com/broken"
    broken = "<html><head></head><body>no og, no json</body></html>"
    client = _client({url: httpx.Response(200, text=broken)})
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="ua",
        page_urls=[url],
        client=client,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_allow_all,
    )
    with pytest.raises(FacebookParseError):
        list(adapter.iter_businesses())


def test_user_agent_sent_on_requests():
    url = "https://www.facebook.com/withua"
    seen: list[str] = []

    def handler(request):
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, text=ACTIVE_PAGE_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    clock = FakeClock()
    adapter = FacebookPagesAdapter(
        user_agent="shmoney-scraper (contact: ops@example.com)",
        page_urls=[url],
        client=client,
        now=clock.now,
        sleep=clock.sleep,
        robots_allows=_allow_all,
    )
    list(adapter.iter_businesses())
    assert seen == ["shmoney-scraper (contact: ops@example.com)"]
