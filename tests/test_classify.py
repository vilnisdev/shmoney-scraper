import pytest

from shmoney.classify import Classification, classify, consulting_opportunity_tag


def test_none_for_null():
    assert classify(None).kind == "none"


def test_none_for_empty():
    assert classify("").kind == "none"
    assert classify("   ").kind == "none"


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://www.facebook.com/joespizza", "facebook"),
        ("https://m.facebook.com/joespizza", "facebook"),
        ("http://facebook.com/joespizza", "facebook"),
        ("https://fb.com/joes", "facebook"),
        ("https://fb.me/joes", "facebook"),
        ("https://www.instagram.com/joespizza/", "instagram"),
        ("https://linktr.ee/joespizza", "linktree"),
        ("https://beacons.ai/joespizza", "beacons"),
    ],
)
def test_social_platforms(url, platform):
    c = classify(url)
    assert c.kind == "social"
    assert c.platform == platform


def test_real_website():
    c = classify("https://joespizza.com")
    assert c.kind == "real"
    assert c.platform is None


def test_bare_host_treated_as_real():
    assert classify("joespizza.com").kind == "real"


def test_tracking_querystring_preserved_in_url():
    c = classify("https://www.facebook.com/joes?utm_source=google&fbclid=abc")
    assert c.kind == "social"
    assert c.platform == "facebook"


def test_trailing_slash_social():
    c = classify("https://www.instagram.com/joes/")
    assert c.kind == "social"


def test_consulting_opportunity_tags():
    assert consulting_opportunity_tag(Classification("none", None, "")) == "no website"
    assert (
        consulting_opportunity_tag(Classification("social", "facebook", "x"))
        == "social-only (facebook)"
    )
    assert consulting_opportunity_tag(Classification("real", None, "x")) == ""
