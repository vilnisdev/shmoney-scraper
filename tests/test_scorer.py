import pytest

from shmoney.audit import AuditFlags, AuditReport
from shmoney.classify import Classification
from shmoney.scorer import score


def _flags(**overrides):
    base = dict(
        reachable=True,
        https=True,
        redirects_to_https=False,
        has_viewport=True,
        response_time_ok=True,
        last_modified_fresh=True,
        body_substantial=True,
    )
    base.update(overrides)
    return AuditFlags(**base)


def _report(**overrides):
    return AuditReport(
        flags=_flags(**overrides),
        fetched_at="2026-04-18T00:00:00",
        status_code=200,
        elapsed_ms=100,
    )


def test_no_website():
    c = Classification(kind="none", platform=None, url="")
    s, tag = score(c, None)
    assert s == 1
    assert tag == "no website"


def test_social_only():
    c = Classification(kind="social", platform="facebook", url="https://facebook.com/x")
    s, tag = score(c, None)
    assert s == 2
    assert tag == "social-only (facebook)"


def test_real_site_pending_audit():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, tag = score(c, None)
    assert s == 3
    assert tag == ""


def test_real_site_all_good():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, tag = score(c, _report())
    assert s == 5
    assert tag == ""


def test_real_site_unreachable():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, tag = score(c, _report(reachable=False, https=False, has_viewport=False,
                              response_time_ok=False, last_modified_fresh=False,
                              body_substantial=False))
    assert s == 1
    assert tag == "site broken"


def test_real_site_http_only_drops_score():
    c = Classification(kind="real", platform=None, url="http://x.com")
    s_secure, _ = score(c, _report())
    s_http, _ = score(c, _report(https=False, redirects_to_https=False))
    assert s_http < s_secure


def test_real_site_redirect_to_https_counts_as_secure():
    c = Classification(kind="real", platform=None, url="http://x.com")
    s, _ = score(c, _report(https=False, redirects_to_https=True))
    assert s == 5


def test_real_site_slow_loses_point():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, _ = score(c, _report(response_time_ok=False))
    assert s == 4


def test_real_site_stale_loses_point():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, _ = score(c, _report(last_modified_fresh=False))
    assert s == 4


def test_real_site_placeholder_low_score():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, tag = score(c, _report(has_viewport=False, body_substantial=False,
                              last_modified_fresh=False, response_time_ok=False))
    assert s <= 2
    assert tag == "weak site"


def test_real_site_score_clamped_1_to_5():
    c = Classification(kind="real", platform=None, url="https://x.com")
    # worst case (but reachable): should not go below 1
    s, _ = score(c, _report(https=False, redirects_to_https=False, has_viewport=False,
                            body_substantial=False, response_time_ok=False,
                            last_modified_fresh=False))
    assert 1 <= s <= 5


def test_real_site_weak_site_tag_when_below_4():
    c = Classification(kind="real", platform=None, url="https://x.com")
    s, tag = score(c, _report(response_time_ok=False, last_modified_fresh=False))
    assert s == 3
    assert tag == "weak site"
