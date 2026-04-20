"""Tests for the SDAT Business Express enrichment adapter.

Fixtures are synthetic (not captured from the live portal). They exercise
parsing contracts the operator can verify against real HTML before the
first live run. Real-site HTML WILL differ — when that happens, capture
fixtures into tests/fixtures/sdat/ and point these tests at them.
"""
from __future__ import annotations

import random
from pathlib import Path

import httpx
import pytest

from shmoney.sources.sdat_direct import (
    ROBOTS_URL,
    SEARCH_URL,
    SdatCircuitBreakerOpen,
    SdatEnricher,
    parse_detail,
    parse_search_results,
    pick_owner,
)


SEARCH_TWO_RESULTS = """
<html><body>
<table id="gvResults">
  <tr><th>ID</th><th>Name</th><th>Status</th><th>Address</th></tr>
  <tr>
    <td>D12345678</td>
    <td><a href="Details?id=D12345678">ACME CORPORATION</a></td>
    <td>Active</td>
    <td>100 Main St, Baltimore MD 21201</td>
  </tr>
  <tr>
    <td>D87654321</td>
    <td><a href="Details?id=D87654321">ACME CORPORATION</a></td>
    <td>Forfeited</td>
    <td>999 Other Rd, Annapolis MD 21401</td>
  </tr>
</table>
</body></html>
"""

SEARCH_EMPTY = '<html><body><table id="gvResults"></table></body></html>'

DETAIL_CORP = """
<html><body>
  <div>Department Name</div><div>ACME CORPORATION</div>
  <div>Department ID</div><div>D12345678</div>
  <div>Entity Type</div><div>Stock Corporation</div>
  <div>Status</div><div>Active</div>
  <div>Formation Date</div><div>2010-05-14</div>
  <div>Principal Office</div><div>100 Main St, Baltimore MD 21201</div>
  <div>Resident Agent Name</div><div>Jane Lawyer Esq.</div>
  <h3>Officers</h3>
  <table>
    <tr><th>Name</th><th>Title</th></tr>
    <tr><td>Alice Founder</td><td>President</td></tr>
    <tr><td>Bob Cofounder</td><td>Secretary</td></tr>
  </table>
</body></html>
"""

DETAIL_LLC = """
<html><body>
  <div>Department Name</div><div>ACME HOLDINGS LLC</div>
  <div>Department ID</div><div>W11112222</div>
  <div>Entity Type</div><div>Domestic LLC</div>
  <div>Status</div><div>Active</div>
  <div>Formation Date</div><div>2018-11-02</div>
  <div>Principal Office</div><div>5 Oak Ave, Columbia MD 21044</div>
  <div>Resident Agent Name</div><div>Carol Owner</div>
</body></html>
"""

DETAIL_FORFEITED = """
<html><body>
  <div>Department Name</div><div>DEAD CO</div>
  <div>Department ID</div><div>D99999999</div>
  <div>Status</div><div>Forfeited</div>
  <div>Formation Date</div><div>1995-03-01</div>
</body></html>
"""

CHALLENGE_PAGE = """
<html><head><title>Just a moment...</title></head>
<body>Cloudflare Attention Required: verify you are human.</body></html>
"""


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


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


# --- parser-level tests ------------------------------------------------------


def test_parse_search_results_extracts_rows():
    rows = parse_search_results(SEARCH_TWO_RESULTS)
    assert len(rows) == 2
    assert rows[0]["dept_id"] == "D12345678"
    assert rows[0]["status"] == "Active"
    assert rows[0]["detail_href"].startswith("Details?id=D12345678")


def test_parse_search_results_empty():
    assert parse_search_results(SEARCH_EMPTY) == []


def test_parse_detail_corp_extracts_officers():
    rec = parse_detail(DETAIL_CORP)
    assert rec is not None
    assert rec.status == "Active"
    assert rec.formation_date == "2010-05-14"
    assert rec.officers[0] == "Alice Founder"
    assert pick_owner(rec) == "Alice Founder"


def test_parse_detail_llc_falls_back_to_resident_agent():
    rec = parse_detail(DETAIL_LLC)
    assert rec is not None
    assert rec.officers == []
    assert rec.resident_agent == "Carol Owner"
    assert pick_owner(rec) == "Carol Owner"


def test_parse_detail_forfeited_status_detected():
    rec = parse_detail(DETAIL_FORFEITED)
    assert rec is not None
    assert "forfeited" in rec.status.lower()


# --- enrich() end-to-end with MockTransport ---------------------------------


def _enricher(handler, **kwargs):
    client = _client(_robots_allow_handler(handler))
    clock = FakeClock()
    return SdatEnricher(
        client=client,
        min_interval_s=4.0,
        jitter_s=0.0,
        now=clock.now,
        sleep=clock.sleep,
        rng=random.Random(0),
        **kwargs,
    ), clock


def test_enrich_happy_path_corp(tmp_path):
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("EntitySearch"):
            return httpx.Response(200, text=SEARCH_TWO_RESULTS)
        return httpx.Response(200, text=DETAIL_CORP)

    enricher, _ = _enricher(handler, cache_dir=tmp_path / "cache")
    rec = enricher.enrich("ACME CORPORATION",
                          address_hint="100 Main St, Baltimore MD 21201")
    assert rec is not None
    assert pick_owner(rec) == "Alice Founder"


def test_enrich_zero_results_returns_none(tmp_path):
    def handler(request):
        return httpx.Response(200, text=SEARCH_EMPTY)
    enricher, _ = _enricher(handler, cache_dir=tmp_path / "cache")
    assert enricher.enrich("NOPE") is None


def test_enrich_429_trips_circuit_breaker(tmp_path):
    def handler(request):
        if request.url.path.endswith("EntitySearch"):
            return httpx.Response(429, headers={"Retry-After": "60"}, text="slow down")
        return httpx.Response(200, text="")

    enricher, _ = _enricher(handler, cache_dir=tmp_path / "cache")
    with pytest.raises(SdatCircuitBreakerOpen):
        enricher.enrich("ACME")


def test_enrich_challenge_page_trips_breaker(tmp_path):
    def handler(request):
        return httpx.Response(200, text=CHALLENGE_PAGE)
    enricher, _ = _enricher(handler, cache_dir=tmp_path / "cache")
    with pytest.raises(SdatCircuitBreakerOpen):
        enricher.enrich("ACME")


def test_enrich_5xx_retries_once_then_gives_up(tmp_path):
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(503, text="down")

    enricher, _ = _enricher(handler, cache_dir=tmp_path / "cache")
    with pytest.raises(SdatCircuitBreakerOpen):
        enricher.enrich("ACME")
    assert hits["n"] == 2  # one attempt + one retry


def test_rate_limit_sleeps_between_fetches(tmp_path):
    def handler(request):
        if request.url.path.endswith("EntitySearch"):
            return httpx.Response(200, text=SEARCH_TWO_RESULTS)
        return httpx.Response(200, text=DETAIL_CORP)

    enricher, clock = _enricher(handler, cache_dir=tmp_path / "cache")
    enricher.enrich("ACME", address_hint="100 Main St 21201")
    # One throttle sleep between search POST and detail GET.
    # jitter=0, min_interval=4 → one 4s sleep expected.
    assert any(abs(s - 4.0) < 0.01 for s in clock.sleeps), clock.sleeps


def test_cache_hits_skip_network(tmp_path):
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        if request.url.path.endswith("EntitySearch"):
            return httpx.Response(200, text=SEARCH_TWO_RESULTS)
        return httpx.Response(200, text=DETAIL_CORP)

    cache = tmp_path / "cache"
    enricher, _ = _enricher(handler, cache_dir=cache)
    enricher.enrich("ACME", address_hint="100 Main St 21201")
    first = hits["n"]
    # Second call with same args — every fetch served from disk.
    enricher2, _ = _enricher(handler, cache_dir=cache)
    enricher2.enrich("ACME", address_hint="100 Main St 21201")
    # Both fetches cached; second enricher makes no new business requests
    # (robots.txt is intercepted by the allow-handler and not counted).
    assert hits["n"] == first


def test_robots_disallow_raises_on_init():
    def handler(request):
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(
                200, text="User-agent: *\nDisallow: /BusinessExpress/\n"
            )
        return httpx.Response(200, text="")
    client = _client(handler)
    with pytest.raises(SdatCircuitBreakerOpen):
        SdatEnricher(client=client, check_robots=True)


def test_dead_candidate_skipped_even_if_top_of_list(tmp_path):
    # When the highest-scored match is Forfeited, the enricher skips it.
    dead_first = """
    <html><body><table id="gvResults">
      <tr><th>ID</th><th>Name</th><th>Status</th><th>Address</th></tr>
      <tr>
        <td>D00000001</td>
        <td><a href="Details?id=D00000001">ACME CORP</a></td>
        <td>Forfeited</td>
        <td>5 Oak Ave, Columbia MD 21044</td>
      </tr>
    </table></body></html>
    """

    def handler(request):
        return httpx.Response(200, text=dead_first)

    enricher, _ = _enricher(handler, cache_dir=tmp_path / "cache")
    assert enricher.enrich("ACME CORP",
                           address_hint="5 Oak Ave Columbia MD 21044") is None
