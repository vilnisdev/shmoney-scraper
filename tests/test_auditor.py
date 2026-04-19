from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from shmoney.audit import WebsiteAuditor


_GOOD_HTML = (
    "<html><head><meta name='viewport' content='width=device-width'></head>"
    "<body>" + ("Welcome to our bakery. " * 40) + "</body></html>"
)
_PLACEHOLDER_HTML = "<html><body>Coming soon.</body></html>"


def _auditor(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=True, timeout=5.0)
    return WebsiteAuditor(client=client)


def test_reachable_https_viewport_fresh_all_true():
    fresh = format_datetime(datetime.now(timezone.utc))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, html=_GOOD_HTML, headers={"last-modified": fresh}
        )

    a = _auditor(handler)
    r = a.audit("https://example.com")
    f = r.flags
    assert f.reachable and f.https and f.has_viewport
    assert f.body_substantial and f.response_time_ok and f.last_modified_fresh
    assert r.status_code == 200


def test_http_only_no_redirect():
    def handler(req):
        return httpx.Response(200, html=_GOOD_HTML)

    r = _auditor(handler).audit("http://example.com")
    assert r.flags.reachable
    assert not r.flags.https
    assert not r.flags.redirects_to_https


def test_http_redirects_to_https():
    def handler(req):
        if req.url.scheme == "http":
            return httpx.Response(
                301, headers={"location": "https://example.com/"}
            )
        return httpx.Response(200, html=_GOOD_HTML)

    r = _auditor(handler).audit("http://example.com")
    assert r.flags.https
    assert r.flags.redirects_to_https


def test_404_unreachable():
    def handler(req):
        return httpx.Response(404, html="not found")

    r = _auditor(handler).audit("https://example.com")
    assert not r.flags.reachable
    assert r.status_code == 404


def test_timeout_unreachable():
    def handler(req):
        raise httpx.ConnectTimeout("boom", request=req)

    r = _auditor(handler).audit("https://example.com")
    assert not r.flags.reachable
    assert r.status_code == 0


def test_placeholder_page():
    def handler(req):
        return httpx.Response(200, html=_PLACEHOLDER_HTML)

    r = _auditor(handler).audit("https://example.com")
    assert r.flags.reachable
    assert not r.flags.has_viewport
    assert not r.flags.body_substantial


def test_stale_last_modified():
    old = format_datetime(datetime.now(timezone.utc) - timedelta(days=800))

    def handler(req):
        return httpx.Response(200, html=_GOOD_HTML, headers={"last-modified": old})

    r = _auditor(handler).audit("https://example.com")
    assert not r.flags.last_modified_fresh


def test_missing_last_modified_treated_fresh():
    def handler(req):
        return httpx.Response(200, html=_GOOD_HTML)

    r = _auditor(handler).audit("https://example.com")
    assert r.flags.last_modified_fresh
