import json
import logging
import re
import time
import urllib.robotparser
from typing import Callable, Iterable, Iterator, Optional
from urllib.parse import urlparse

import httpx

from .base import RawBusiness, SourceAdapter


log = logging.getLogger(__name__)


class FacebookParseError(RuntimeError):
    pass


_OG_TITLE_RE = re.compile(
    r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_CATEGORY_RE = re.compile(
    r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _default_robots_allows(user_agent: str) -> Callable[[str], bool]:
    cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _allows(url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        rp = cache.get(root)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{root}/robots.txt")
            try:
                rp.read()
            except Exception:
                return False
            cache[root] = rp
        return rp.can_fetch(user_agent, url)

    return _allows


def _find_local_business(obj):
    if isinstance(obj, dict):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and "Business" in x for x in types):
            return obj
        for v in obj.values():
            found = _find_local_business(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_local_business(v)
            if found is not None:
                return found
    return None


def _format_address(addr) -> str:
    if not isinstance(addr, dict):
        return ""
    parts = [
        addr.get("streetAddress"),
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("postalCode"),
    ]
    return ", ".join(p for p in parts if p)


def _parse_page(html: str, page_url: str) -> RawBusiness:
    ld_block = None
    for match in _JSON_LD_RE.finditer(html):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidate = _find_local_business(data)
        if candidate is not None:
            ld_block = candidate
            break

    og_title_m = _OG_TITLE_RE.search(html)
    og_desc_m = _OG_CATEGORY_RE.search(html)

    name = None
    if ld_block and ld_block.get("name"):
        name = ld_block["name"]
    elif og_title_m:
        name = og_title_m.group(1)

    if not name:
        raise FacebookParseError(
            f"could not extract business name from {page_url}; selectors may be stale"
        )

    address = _format_address(ld_block.get("address") if ld_block else None)
    phone = ld_block.get("telephone") if ld_block else None
    external = ld_block.get("url") if ld_block else None
    category = None
    if og_desc_m:
        category = og_desc_m.group(1)
    elif ld_block and ld_block.get("priceRange"):
        category = None

    website = external or page_url

    return RawBusiness(
        source="facebook",
        name=name,
        address=address,
        phone=phone,
        website=website,
        business_type=category,
        yelp_or_google_listing=page_url,
        raw={"page_url": page_url, "ld": ld_block},
    )


class FacebookPagesAdapter(SourceAdapter):
    source_name = "facebook"

    def __init__(
        self,
        user_agent: str,
        page_urls: Iterable[str],
        client: Optional[httpx.Client] = None,
        min_interval_s: float = 3.0,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        robots_allows: Optional[Callable[[str], bool]] = None,
        limit: Optional[int] = None,
    ):
        if not user_agent:
            raise ValueError("user_agent required")
        self.user_agent = user_agent
        self.page_urls = list(page_urls)
        self.min_interval_s = min_interval_s
        self._now = now
        self._sleep = sleep
        self._robots_allows = robots_allows or _default_robots_allows(user_agent)
        self.limit = limit
        if client is None:
            client = httpx.Client(
                headers={"User-Agent": user_agent},
                timeout=15.0,
                follow_redirects=True,
            )
        self._client = client

    def iter_businesses(self) -> Iterator[RawBusiness]:
        last_fetch_at: dict[str, float] = {}
        denied_hosts: set[str] = set()
        emitted = 0
        for url in self.page_urls:
            if self.limit is not None and emitted >= self.limit:
                break
            host = urlparse(url).netloc.lower()
            if host in denied_hosts:
                continue
            if not self._robots_allows(url):
                log.info("robots.txt disallow for host %s; skipping host", host)
                denied_hosts.add(host)
                continue

            last = last_fetch_at.get(host)
            if last is not None:
                elapsed = self._now() - last
                wait = self.min_interval_s - elapsed
                if wait > 0:
                    self._sleep(wait)
            last_fetch_at[host] = self._now()

            try:
                resp = self._client.get(url, headers={"User-Agent": self.user_agent})
            except httpx.HTTPError as e:
                log.warning("fetch failed for %s: %s", url, e)
                continue

            if resp.status_code == 404:
                log.info("facebook page 404/takedown: %s", url)
                continue
            if resp.status_code >= 400:
                log.warning("facebook fetch %s returned %s", url, resp.status_code)
                continue

            yield _parse_page(resp.text, url)
            emitted += 1
