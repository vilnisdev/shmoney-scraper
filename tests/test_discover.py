"""Tests for the DuckDuckGo-backed website discovery module."""
from __future__ import annotations

import random

import httpx
import pytest

from shmoney.discover import (
    WebsiteDiscoverer,
    WebsiteDiscoveryCircuitBreakerOpen,
    _host_of,
    _name_overlap,
    _tokenize_name,
    parse_ddg_results,
)


DDG_TWO_HITS_TEMPLATE = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//paniaguaenterprises.com/">Paniagua Enterprises Baltimore | Construction</a>
  <a class="result__snippet">Paniagua Enterprises Inc — general contracting in Baltimore, MD.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//www.yelp.com/biz/paniagua-enterprises">Paniagua Enterprises | Yelp</a>
  <a class="result__snippet">Reviews of Paniagua Enterprises in Baltimore.</a>
</div>
</body></html>
"""

DDG_NAMESAKE_TEMPLATE = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//namesake-tx.example/">Paniagua Enterprises — Austin TX</a>
  <a class="result__snippet">Paniagua Enterprises serving the greater Austin area.</a>
</div>
</body></html>
"""

HOMEPAGE_HAS_BALTIMORE = """
<html><head><title>Paniagua</title></head>
<body>
<p>We serve Baltimore and surrounding areas.</p>
<p>Call us at 410-555-0000.</p>
</body></html>
"""

HOMEPAGE_NO_CITY = "<html><body><p>Welcome to our Texas contracting firm.</p></body></html>"

CHALLENGE_HTML = "<html><body>Please verify you're human (cloudflare captcha).</body></html>"


def _transport(handler):
    return httpx.MockTransport(handler)


def _client(handler):
    return httpx.Client(transport=_transport(handler), follow_redirects=True)


def _robots_allow_handler(inner):
    def handler(request):
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return inner(request)
    return handler


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _discoverer(handler, **kwargs):
    client = _client(_robots_allow_handler(handler))
    clock = FakeClock()
    return WebsiteDiscoverer(
        client=client,
        min_interval_s=3.0,
        jitter_s=0.0,
        now=clock.now,
        sleep=clock.sleep,
        rng=random.Random(0),
        **kwargs,
    ), clock


# --- pure functions ----------------------------------------------------------


def test_tokenize_name_strips_entity_suffixes():
    assert _tokenize_name("Paniagua Enterprises, Inc.") == {"paniagua", "enterprises"}


def test_tokenize_name_drops_stopwords():
    # The, of, a (via stopwords), b (single-char), & (non-alnum), LLC (stopword),
    # Company (stopword) → only "ab" would... actually nothing ≥2-char survives.
    assert _tokenize_name("The Company of A&B LLC") == set()
    # Realistic case:
    assert _tokenize_name("The Ruma Company LLC") == {"ruma"}


def test_name_overlap_full_match():
    assert _name_overlap("Paniagua Enterprises Inc", "Paniagua Enterprises Baltimore") == 1.0


def test_name_overlap_partial():
    # "Joe's Pizza and Pasta" → {"joes", "pizza", "pasta"}. Title has 2/3.
    assert _name_overlap("Joe's Pizza and Pasta", "Joe's Pizza Baltimore") == pytest.approx(2 / 3)


def test_host_of_strips_www():
    assert _host_of("https://www.foo.com/bar") == "foo.com"


def test_parse_ddg_results_extracts_two():
    results = parse_ddg_results(DDG_TWO_HITS_TEMPLATE)
    assert len(results) == 2
    assert results[0]["url"] == "https://paniaguaenterprises.com/"
    assert "yelp.com" in results[1]["url"]


# --- discovery end-to-end ---------------------------------------------------


def test_discover_happy_path(tmp_path):
    def handler(request):
        url = str(request.url)
        if "duckduckgo.com/html" in url:
            return httpx.Response(200, text=DDG_TWO_HITS_TEMPLATE)
        if "paniaguaenterprises.com" in url:
            return httpx.Response(200, text=HOMEPAGE_HAS_BALTIMORE)
        return httpx.Response(200, text="")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    result = discoverer.discover(
        "Paniagua Enterprises, Inc.",
        address="123 Main St, Baltimore, MD 21201",
        phone="410-555-0000",
    )
    assert result is not None
    assert "paniaguaenterprises.com" in result.url
    # Domain match short-circuits the homepage verification.
    assert any("domain_overlap" in r for r in result.reasons)


def test_discover_rejects_social_domain(tmp_path):
    # Only hit is Yelp — must be rejected.
    ddg_only_yelp = """
    <html><body>
      <div class="result">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//www.yelp.com/biz/x">X</a>
        <a class="result__snippet">X — Baltimore</a>
      </div>
    </body></html>
    """

    def handler(request):
        if "duckduckgo.com/html" in str(request.url):
            return httpx.Response(200, text=ddg_only_yelp)
        return httpx.Response(200, text=HOMEPAGE_HAS_BALTIMORE)

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    assert discoverer.discover(
        "X", address="1 Main St, Baltimore, MD 21201"
    ) is None


def test_discover_accepts_when_domain_contains_name_tokens(tmp_path):
    # Regression for false-negative on Synergy Systems & Services, Inc. —
    # homepage doesn't mention Baltimore but the domain name does match.
    ddg_synergy = """
    <html><body>
      <div class="result">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//synergysystems.com/">Synergy Systems &amp; Services Inc — Home</a>
        <a class="result__snippet">Synergy Systems and Services Inc.</a>
      </div>
    </body></html>
    """
    bland_homepage = "<html><body><h1>Welcome</h1><p>Enterprise consulting.</p></body></html>"

    def handler(request):
        if "duckduckgo.com/html" in str(request.url):
            return httpx.Response(200, text=ddg_synergy)
        return httpx.Response(200, text=bland_homepage)

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    result = discoverer.discover(
        "Synergy Systems & Services, Inc.",
        address="123 Main St, Baltimore, MD 21201",
    )
    assert result is not None
    assert "synergysystems.com" in result.url
    assert any("domain_overlap" in r for r in result.reasons)


def test_discover_falls_back_to_contact_page(tmp_path):
    # Root homepage has no geo signal; /contact does. Must accept via fallback.
    # Design: name tokens = {acme, plumbing, baltimore, contractors}.
    #   title+snippet covers all 4 → title_overlap = 1.0 (passes).
    #   domain "servicefirm" covers 0/4 → fails domain gate → forces
    #   location verification path; root fails, /contact hits.
    ddg = """
    <html><body>
      <div class="result">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//servicefirm.example/">Acme Plumbing Baltimore Contractors</a>
        <a class="result__snippet">Acme Plumbing Baltimore Contractors service roster.</a>
      </div>
    </body></html>
    """

    def handler(request):
        url = str(request.url)
        if "duckduckgo.com/html" in url:
            return httpx.Response(200, text=ddg)
        if url.endswith("/contact"):
            return httpx.Response(200, text="<p>Baltimore, MD office.</p>")
        # Root homepage + other paths: no geo signal.
        return httpx.Response(200, text="<html><body>Welcome</body></html>")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    result = discoverer.discover(
        "Acme Plumbing Baltimore Contractors",
        address="1 Main St, Baltimore, MD 21201",
    )
    assert result is not None
    assert any("city_match" in r for r in result.reasons)


def test_discover_rejects_namesake_in_wrong_city(tmp_path):
    # Name matches but homepage doesn't mention Baltimore / zip / phone.
    def handler(request):
        if "duckduckgo.com/html" in str(request.url):
            return httpx.Response(200, text=DDG_NAMESAKE_TEMPLATE)
        return httpx.Response(200, text=HOMEPAGE_NO_CITY)

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    assert discoverer.discover(
        "Paniagua Enterprises",
        address="123 Main St, Baltimore, MD 21201",
        phone="410-555-0000",
    ) is None


def test_discover_rejects_low_name_overlap(tmp_path):
    # Title barely mentions the business.
    weak_html = """
    <html><body>
      <div class="result">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//example.com/">Completely unrelated title</a>
        <a class="result__snippet">Nothing about the business.</a>
      </div>
    </body></html>
    """

    def handler(request):
        if "duckduckgo.com/html" in str(request.url):
            return httpx.Response(200, text=weak_html)
        return httpx.Response(200, text=HOMEPAGE_HAS_BALTIMORE)

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    assert discoverer.discover(
        "Paniagua Enterprises Inc",
        address="1 Main St, Baltimore, MD 21201",
    ) is None


def test_discover_429_trips_breaker(tmp_path):
    def handler(request):
        return httpx.Response(429, text="slow down")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    with pytest.raises(WebsiteDiscoveryCircuitBreakerOpen):
        discoverer.discover("X", "1 Main St, Baltimore, MD")


def test_discover_challenge_page_trips_breaker(tmp_path):
    def handler(request):
        return httpx.Response(200, text=CHALLENGE_HTML)

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    with pytest.raises(WebsiteDiscoveryCircuitBreakerOpen):
        discoverer.discover("X", "1 Main St, Baltimore, MD")


def test_discover_rate_limit_sleeps_between_fetches(tmp_path):
    # Use a name whose domain doesn't match strongly, so the verification
    # path forces multiple HTTP calls (DDG + homepage + fallback paths),
    # exercising the throttle.
    ddg = """
    <html><body>
      <div class="result">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//genericsite.example/">Acme Plumbing Baltimore Contractors</a>
        <a class="result__snippet">Acme Plumbing Baltimore Contractors service.</a>
      </div>
    </body></html>
    """

    def handler(request):
        url = str(request.url)
        if "duckduckgo.com/html" in url:
            return httpx.Response(200, text=ddg)
        if url.endswith("/contact"):
            return httpx.Response(200, text="<p>Baltimore office.</p>")
        return httpx.Response(200, text="<html><body>Welcome</body></html>")

    discoverer, clock = _discoverer(handler, cache_dir=tmp_path / "c")
    discoverer.discover(
        "Acme Plumbing Baltimore Contractors",
        address="1 Main St, Baltimore, MD 21201",
    )
    # At least one 3.0s throttle delay between fetches.
    assert any(abs(s - 3.0) < 0.01 for s in clock.sleeps), clock.sleeps


def test_discover_cache_hits_skip_network(tmp_path):
    hits = {"n": 0}

    def handler(request):
        if not request.url.path.endswith("/robots.txt"):
            hits["n"] += 1
        url = str(request.url)
        if "duckduckgo.com/html" in url:
            return httpx.Response(200, text=DDG_TWO_HITS_TEMPLATE)
        return httpx.Response(200, text=HOMEPAGE_HAS_BALTIMORE)

    cache = tmp_path / "c"
    d1, _ = _discoverer(handler, cache_dir=cache)
    d1.discover(
        "Paniagua Enterprises, Inc.",
        address="1 Main St, Baltimore, MD 21201",
    )
    first = hits["n"]
    d2, _ = _discoverer(handler, cache_dir=cache)
    d2.discover(
        "Paniagua Enterprises, Inc.",
        address="1 Main St, Baltimore, MD 21201",
    )
    assert hits["n"] == first  # all served from disk


def test_robots_disallow_raises_on_init():
    def handler(request):
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="")

    with pytest.raises(WebsiteDiscoveryCircuitBreakerOpen):
        WebsiteDiscoverer(client=_client(handler), check_robots=True)
