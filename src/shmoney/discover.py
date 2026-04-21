"""Brave-Search-backed website discovery for rows where adapter feeds didn't
supply a URL.

Strict match: non-social domain + name-token overlap + domain-name overlap
OR homepage (root + /contact + /about) mentions city/zip/phone.

Brave Search API has a proper free tier (2000 queries/month, 1 qps), JSON
responses, and no anti-bot friction. Replaces the earlier DuckDuckGo scrape
which was being silently shimmed then blocked (see issue #38).
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx


BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 (+shmoney-scraper; contact: operator)"
)

_SOCIAL_AND_DIRECTORY_BLOCKLIST = frozenset(
    {
        "facebook.com", "m.facebook.com", "fb.com", "fb.me",
        "instagram.com", "m.instagram.com",
        "twitter.com", "x.com",
        "linkedin.com",
        "yelp.com", "m.yelp.com",
        "bbb.org",
        "manta.com", "dnb.com",
        "yellowpages.com", "superpages.com",
        "mapquest.com",
        "chamberofcommerce.com",
        "tripadvisor.com",
        "glassdoor.com", "indeed.com",
        "crunchbase.com",
        "youtube.com", "pinterest.com",
        "brave.com", "duckduckgo.com", "google.com", "bing.com",
    }
)

_NAME_STOPWORDS = frozenset(
    {
        "llc", "inc", "incorporated", "corp", "corporation", "co", "company",
        "the", "and", "&", "of", "a", "an",
    }
)

_CHALLENGE_MARKERS = (
    "captcha",
    "are you a robot",
    "verify you're human",
    "access denied",
    "cloudflare",
    "attention required",
    "just a moment",
)

_FALLBACK_PATHS = ("/contact", "/contact-us", "/about", "/about-us")


class WebsiteDiscoveryCircuitBreakerOpen(RuntimeError):
    """Abort signal — 429 / challenge / repeated 5xx / auth failure / budget exhausted."""


@dataclass
class DiscoveryResult:
    url: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _tokenize_name(name: str) -> set[str]:
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = {t for t in s.split() if len(t) >= 2 and t not in _NAME_STOPWORDS}
    return tokens


def _name_overlap(name: str, candidate_text: str) -> float:
    tokens = _tokenize_name(name)
    if not tokens:
        return 0.0
    cand = candidate_text.lower()
    hits = sum(1 for t in tokens if t in cand)
    return hits / len(tokens)


def _domain_name_overlap(name: str, host: str) -> float:
    tokens = _tokenize_name(name)
    if not tokens:
        return 0.0
    host_core = host.split(".")[0] if host else ""
    hits = sum(1 for t in tokens if t in host_core)
    return hits / len(tokens)


def _extract_city(address: str) -> Optional[str]:
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        return parts[-2] or None
    return None


def _extract_zip(address: str) -> Optional[str]:
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
    return m.group(1) if m else None


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else None


def _looks_like_challenge(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in _CHALLENGE_MARKERS)


def _cache_key(method: str, url: str, body: str) -> str:
    h = hashlib.sha1()
    h.update(method.encode())
    h.update(b"|")
    h.update(url.encode())
    h.update(b"|")
    h.update(body.encode())
    return h.hexdigest()


def _host_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def parse_brave_results(payload_text: str) -> list[dict]:
    """Brave returns JSON: {"web": {"results": [{title, description, url}, ...]}}.
    Normalize to [{title, snippet, url}] with at most 5 entries."""
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    web = data.get("web") or {}
    raw = web.get("results") or []
    out: list[dict] = []
    for item in raw[:5]:
        out.append({
            "title": (item.get("title") or "").strip(),
            "snippet": (item.get("description") or "").strip(),
            "url": (item.get("url") or "").strip(),
        })
    return out


class WebsiteDiscoverer:
    def __init__(
        self,
        api_key: str,
        client: Optional[httpx.Client] = None,
        min_interval_s: float = 1.1,
        jitter_s: float = 0.2,
        user_agent: str = DEFAULT_UA,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
        cache_dir: Optional[Path] = None,
        query_budget: Optional[int] = None,
        on_query: Optional[Callable[[], int]] = None,
    ):
        if not api_key:
            raise ValueError("BRAVE_API_KEY required")
        self.api_key = api_key
        self._query_budget = query_budget
        self._on_query = on_query
        self._queries_this_session = 0
        self.min_interval_s = min_interval_s
        self.jitter_s = jitter_s
        self.user_agent = user_agent
        self._now = now
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_fetch_at: Optional[float] = None
        self._client = client if client is not None else httpx.Client(
            timeout=30.0,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )

    def _throttle(self) -> None:
        if self._last_fetch_at is None:
            return
        delay = self.min_interval_s + self._rng.uniform(-self.jitter_s, self.jitter_s)
        delay = max(delay, 0.0)
        wait = delay - (self._now() - self._last_fetch_at)
        if wait > 0:
            self._sleep(wait)

    def _cache_read(self, key: str) -> Optional[str]:
        if not self._cache_dir:
            return None
        p = self._cache_dir / f"{key}.html"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _cache_write(self, key: str, text: str) -> None:
        if not self._cache_dir:
            return
        p = self._cache_dir / f"{key}.html"
        p.write_text(text, encoding="utf-8")

    def _fetch(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> str:
        body = urllib.parse.urlencode(params or {}, doseq=True)
        key = _cache_key("GET", url, body)
        cached = self._cache_read(key)
        if cached is not None:
            return cached

        self._throttle()

        attempts = 0
        while True:
            attempts += 1
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as e:
                if attempts >= 2:
                    raise WebsiteDiscoveryCircuitBreakerOpen(f"network error: {e}") from e
                self._sleep(10.0)
                continue
            finally:
                self._last_fetch_at = self._now()

            status = resp.status_code
            text = resp.text

            if status == 429:
                raise WebsiteDiscoveryCircuitBreakerOpen(
                    f"429; retry-after={resp.headers.get('Retry-After')}"
                )
            if status in (401, 403):
                raise WebsiteDiscoveryCircuitBreakerOpen(
                    f"auth failure {status} — check BRAVE_API_KEY"
                )
            if 500 <= status < 600:
                if attempts >= 2:
                    raise WebsiteDiscoveryCircuitBreakerOpen(f"{status} after retry")
                self._sleep(10.0)
                continue
            if status >= 400:
                raise WebsiteDiscoveryCircuitBreakerOpen(f"unexpected status {status}")
            if _looks_like_challenge(text):
                raise WebsiteDiscoveryCircuitBreakerOpen("challenge page detected")

            self._cache_write(key, text)
            return text

    def _search(self, query: str) -> list[dict]:
        # Budget check + persistent counter bump BEFORE issuing the request.
        # Cached lookups don't hit the API, so we only count on cache miss.
        params = {"q": query, "count": "5"}
        body = urllib.parse.urlencode(params, doseq=True)
        key = _cache_key("GET", BRAVE_SEARCH_URL, body)
        if self._cache_read(key) is None:
            if (
                self._query_budget is not None
                and self._queries_this_session >= self._query_budget
            ):
                raise WebsiteDiscoveryCircuitBreakerOpen(
                    f"Brave query budget exhausted ({self._query_budget})"
                )
            self._queries_this_session += 1
            if self._on_query is not None:
                self._on_query()
        text = self._fetch(
            BRAVE_SEARCH_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
        )
        return parse_brave_results(text)

    def discover(
        self,
        name: str,
        address: str,
        phone: Optional[str] = None,
    ) -> Optional[DiscoveryResult]:
        city = _extract_city(address)
        zipc = _extract_zip(address)
        phone_digits = _normalize_phone(phone)

        query = f'"{name}"'
        if city:
            query += f" {city}"
        elif zipc:
            query += f" {zipc}"

        candidates = self._search(query)

        best: Optional[DiscoveryResult] = None
        for c in candidates:
            host = _host_of(c["url"])
            if not host:
                continue
            if host in _SOCIAL_AND_DIRECTORY_BLOCKLIST:
                continue
            title_overlap = _name_overlap(name, c["title"] + " " + c["snippet"])
            if title_overlap < 0.7:
                continue

            reasons = [f"title_overlap={title_overlap:.2f}", f"host={host}"]

            domain_overlap = _domain_name_overlap(name, host)
            if domain_overlap >= 0.5:
                reasons.append(f"domain_overlap={domain_overlap:.2f}")
                candidate = DiscoveryResult(
                    url=c["url"], confidence=title_overlap, reasons=reasons
                )
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
                continue

            verified, verify_reasons = self._verify_location(
                c["url"], city, zipc, phone_digits
            )
            if not verified:
                continue
            reasons.extend(verify_reasons)
            candidate = DiscoveryResult(
                url=c["url"], confidence=title_overlap, reasons=reasons
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    def _verify_location(
        self,
        root_url: str,
        city: Optional[str],
        zipc: Optional[str],
        phone_digits: Optional[str],
    ) -> tuple[bool, list[str]]:
        parsed = urllib.parse.urlparse(root_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        urls_to_try = [root_url] + [base + p for p in _FALLBACK_PATHS]
        for url in urls_to_try:
            try:
                html = self._fetch(url)
            except WebsiteDiscoveryCircuitBreakerOpen:
                raise
            except Exception:
                continue
            html_lower = html.lower()
            city_hit = bool(city and city.lower() in html_lower)
            zip_hit = bool(zipc and zipc in html_lower)
            phone_hit = False
            if phone_digits:
                html_digits = re.sub(r"\D", "", html_lower)
                phone_hit = phone_digits in html_digits
            if city_hit or zip_hit or phone_hit:
                reasons = []
                suffix = "" if url == root_url else f" ({parsed.path or '/'})"
                if city_hit:
                    reasons.append(f"city_match{suffix}")
                if zip_hit:
                    reasons.append(f"zip_match{suffix}")
                if phone_hit:
                    reasons.append(f"phone_match{suffix}")
                return True, reasons
        return False, []

    def close(self) -> None:
        self._client.close()
