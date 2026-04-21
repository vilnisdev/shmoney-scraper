"""Tests for the Brave-Search-backed website discovery module."""
from __future__ import annotations

import json
import random

import httpx
import pytest

from shmoney.discover import (
    BRAVE_SEARCH_URL,
    WebsiteDiscoverer,
    WebsiteDiscoveryCircuitBreakerOpen,
    _domain_name_overlap,
    _host_of,
    _name_overlap,
    _tokenize_name,
    parse_brave_results,
)


def _brave_payload(results):
    return json.dumps({"web": {"results": results}})


def _transport(handler):
    return httpx.MockTransport(handler)


def _client(handler):
    return httpx.Client(transport=_transport(handler), follow_redirects=True)


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
    client = _client(handler)
    clock = FakeClock()
    return WebsiteDiscoverer(
        api_key="test-key",
        client=client,
        min_interval_s=1.1,
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
    assert _tokenize_name("The Company of A&B LLC") == set()
    assert _tokenize_name("The Ruma Company LLC") == {"ruma"}


def test_name_overlap_full_match():
    assert _name_overlap("Paniagua Enterprises Inc", "Paniagua Enterprises Baltimore") == 1.0


def test_name_overlap_partial():
    assert _name_overlap("Joe's Pizza and Pasta", "Joe's Pizza Baltimore") == pytest.approx(2 / 3)


def test_host_of_strips_www():
    assert _host_of("https://www.foo.com/bar") == "foo.com"


def test_domain_overlap_contains_tokens():
    assert _domain_name_overlap("Synergy Systems Inc", "synergysystems.com") == pytest.approx(1.0)


def test_domain_overlap_no_match():
    assert _domain_name_overlap("Paniagua Enterprises", "namesake-tx.example") == 0.0


def test_parse_brave_results_extracts_entries():
    payload = _brave_payload([
        {"title": "Foo Inc", "description": "Makes foo.", "url": "https://foo.com/"},
        {"title": "Bar", "description": "Bar site.", "url": "https://bar.example/"},
    ])
    results = parse_brave_results(payload)
    assert len(results) == 2
    assert results[0]["url"] == "https://foo.com/"
    assert results[0]["snippet"] == "Makes foo."


def test_parse_brave_results_handles_empty():
    assert parse_brave_results('{"web": {"results": []}}') == []
    assert parse_brave_results("{}") == []
    assert parse_brave_results("not json") == []


# --- discovery end-to-end ---------------------------------------------------


def test_discover_happy_path_via_domain_match(tmp_path):
    def handler(request):
        if BRAVE_SEARCH_URL in str(request.url):
            assert request.headers.get("X-Subscription-Token") == "test-key"
            return httpx.Response(200, text=_brave_payload([
                {
                    "title": "Paniagua Enterprises Baltimore | Construction",
                    "description": "General contracting.",
                    "url": "https://paniaguaenterprises.com/",
                },
            ]))
        return httpx.Response(200, text="<html>Welcome</html>")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    result = discoverer.discover(
        "Paniagua Enterprises, Inc.",
        address="123 Main St, Baltimore, MD 21201",
    )
    assert result is not None
    assert "paniaguaenterprises.com" in result.url
    assert any("domain_overlap" in r for r in result.reasons)


def test_discover_rejects_social_domain(tmp_path):
    def handler(request):
        if BRAVE_SEARCH_URL in str(request.url):
            return httpx.Response(200, text=_brave_payload([
                {"title": "X Co — Baltimore", "description": "",
                 "url": "https://www.yelp.com/biz/x"},
            ]))
        return httpx.Response(200, text="")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    assert discoverer.discover(
        "X", address="1 Main St, Baltimore, MD 21201"
    ) is None


def test_discover_rejects_low_name_overlap(tmp_path):
    def handler(request):
        if BRAVE_SEARCH_URL in str(request.url):
            return httpx.Response(200, text=_brave_payload([
                {"title": "Unrelated", "description": "Nothing matches.",
                 "url": "https://example.com/"},
            ]))
        return httpx.Response(200, text="")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    assert discoverer.discover(
        "Paniagua Enterprises Inc",
        address="1 Main St, Baltimore, MD 21201",
    ) is None


def test_discover_falls_back_to_contact_page(tmp_path):
    def handler(request):
        url = str(request.url)
        if BRAVE_SEARCH_URL in url:
            return httpx.Response(200, text=_brave_payload([
                {
                    "title": "Acme Plumbing Baltimore Contractors",
                    "description": "Acme Plumbing Baltimore Contractors service roster.",
                    "url": "https://servicefirm.example/",
                },
            ]))
        if url.endswith("/contact"):
            return httpx.Response(200, text="<p>Baltimore, MD office.</p>")
        return httpx.Response(200, text="<html>Welcome</html>")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    result = discoverer.discover(
        "Acme Plumbing Baltimore Contractors",
        address="1 Main St, Baltimore, MD 21201",
    )
    assert result is not None
    assert any("city_match" in r for r in result.reasons)


def test_discover_rejects_namesake_in_wrong_city(tmp_path):
    def handler(request):
        if BRAVE_SEARCH_URL in str(request.url):
            return httpx.Response(200, text=_brave_payload([
                {
                    "title": "Paniagua Enterprises — Austin TX",
                    "description": "Paniagua Enterprises serving Austin area.",
                    "url": "https://namesake-tx.example/",
                },
            ]))
        return httpx.Response(200, text="<p>Texas contracting firm.</p>")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    assert discoverer.discover(
        "Paniagua Enterprises",
        address="123 Main St, Baltimore, MD 21201",
    ) is None


def test_discover_429_trips_breaker(tmp_path):
    def handler(request):
        return httpx.Response(429, text="slow down")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    with pytest.raises(WebsiteDiscoveryCircuitBreakerOpen):
        discoverer.discover("X", "1 Main St, Baltimore, MD")


def test_discover_401_raises_auth_failure(tmp_path):
    def handler(request):
        return httpx.Response(401, text="bad key")

    discoverer, _ = _discoverer(handler, cache_dir=tmp_path / "c")
    with pytest.raises(WebsiteDiscoveryCircuitBreakerOpen, match="auth failure"):
        discoverer.discover("X", "1 Main St, Baltimore, MD")


def test_discover_rate_limit_sleeps_between_fetches(tmp_path):
    def handler(request):
        url = str(request.url)
        if BRAVE_SEARCH_URL in url:
            return httpx.Response(200, text=_brave_payload([
                {
                    "title": "Acme Plumbing Baltimore Contractors",
                    "description": "Acme Plumbing Baltimore Contractors service.",
                    "url": "https://servicefirm.example/",
                },
            ]))
        if url.endswith("/contact"):
            return httpx.Response(200, text="<p>Baltimore office.</p>")
        return httpx.Response(200, text="<html>Welcome</html>")

    discoverer, clock = _discoverer(handler, cache_dir=tmp_path / "c")
    discoverer.discover(
        "Acme Plumbing Baltimore Contractors",
        address="1 Main St, Baltimore, MD 21201",
    )
    assert any(abs(s - 1.1) < 0.01 for s in clock.sleeps), clock.sleeps


def test_discover_cache_hits_skip_network(tmp_path):
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        url = str(request.url)
        if BRAVE_SEARCH_URL in url:
            return httpx.Response(200, text=_brave_payload([
                {
                    "title": "Paniagua Enterprises Baltimore",
                    "description": "",
                    "url": "https://paniaguaenterprises.com/",
                },
            ]))
        return httpx.Response(200, text="<html>Welcome</html>")

    cache = tmp_path / "c"
    d1, _ = _discoverer(handler, cache_dir=cache)
    d1.discover("Paniagua Enterprises", "1 Main St, Baltimore, MD 21201")
    first = hits["n"]
    d2, _ = _discoverer(handler, cache_dir=cache)
    d2.discover("Paniagua Enterprises", "1 Main St, Baltimore, MD 21201")
    assert hits["n"] == first


def test_missing_api_key_raises():
    with pytest.raises(ValueError, match="BRAVE_API_KEY"):
        WebsiteDiscoverer(api_key="")


def test_query_budget_stops_after_n(tmp_path):
    bumps = {"n": 0}

    def handler(request):
        return httpx.Response(200, text=_brave_payload([
            {"title": "x", "description": "", "url": "https://x.example/"},
        ]))

    discoverer, _ = _discoverer(
        handler,
        cache_dir=tmp_path / "c",
        query_budget=2,
        on_query=lambda: bumps.__setitem__("n", bumps["n"] + 1),
    )
    discoverer.discover("Biz One", "1 Main St, Baltimore, MD 21201")
    discoverer.discover("Biz Two", "2 Main St, Baltimore, MD 21201")
    assert bumps["n"] == 2
    with pytest.raises(WebsiteDiscoveryCircuitBreakerOpen, match="budget"):
        discoverer.discover("Biz Three", "3 Main St, Baltimore, MD 21201")
    assert bumps["n"] == 2  # not bumped for the aborted call


def test_cached_searches_dont_consume_budget(tmp_path):
    bumps = {"n": 0}

    def handler(request):
        return httpx.Response(200, text=_brave_payload([
            {"title": "x", "description": "", "url": "https://x.example/"},
        ]))

    cache = tmp_path / "c"
    d1, _ = _discoverer(
        handler,
        cache_dir=cache,
        query_budget=10,
        on_query=lambda: bumps.__setitem__("n", bumps["n"] + 1),
    )
    d1.discover("Biz One", "1 Main St, Baltimore, MD 21201")
    assert bumps["n"] == 1

    d2, _ = _discoverer(
        handler,
        cache_dir=cache,
        query_budget=10,
        on_query=lambda: bumps.__setitem__("n", bumps["n"] + 1),
    )
    d2.discover("Biz One", "1 Main St, Baltimore, MD 21201")
    assert bumps["n"] == 1  # cache hit, no bump
