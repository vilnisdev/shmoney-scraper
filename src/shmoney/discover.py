"""DuckDuckGo-backed website discovery for rows where adapter feeds didn't
supply a URL. Strict match: non-social domain + name-token overlap +
homepage mentions city/zip/phone.

Ban-safety mirrors the closed PR #28 SdatEnricher pattern: throttle +
jitter + cache + circuit breaker + challenge detection.
"""
from __future__ import annotations

import hashlib
import random
import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx
from bs4 import BeautifulSoup


DDG_URL = "https://html.duckduckgo.com/html/"

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
        "duckduckgo.com", "google.com",
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


class WebsiteDiscoveryCircuitBreakerOpen(RuntimeError):
    """Abort signal — 429 / challenge / repeated 5xx / robots disallow."""


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
    """How many business-name tokens appear somewhere in the domain label.
    `synergysystems.com` vs "Synergy Systems Inc" → 1.0 (2/2 tokens)."""
    tokens = _tokenize_name(name)
    if not tokens:
        return 0.0
    host_core = host.split(".")[0] if host else ""
    hits = sum(1 for t in tokens if t in host_core)
    return hits / len(tokens)


_FALLBACK_PATHS = ("/contact", "/contact-us", "/about", "/about-us")


def _extract_city(address: str) -> Optional[str]:
    # Expect "<street>, <city>, <state> <zip>" — pick the city segment.
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


def parse_ddg_results(html: str) -> list[dict]:
    """DDG's /html/ endpoint returns a table of `<a class="result__a">` title
    links with adjacent `<a class="result__snippet">` snippets. Host
    exposed via the redirect URL's `uddg` query param."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for block in soup.select(".result"):
        a = block.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href") or ""
        title = a.get_text(" ", strip=True)
        snippet_el = block.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        # DDG wraps outbound links in /l/?uddg=<encoded-url>.
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg") or []
        url = uddg[0] if uddg else href
        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= 5:
            break
    return results


class WebsiteDiscoverer:
    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        min_interval_s: float = 3.0,
        jitter_s: float = 1.0,
        user_agent: str = DEFAULT_UA,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
        cache_dir: Optional[Path] = None,
        check_robots: bool = True,
    ):
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
            timeout=30.0, headers={"User-Agent": user_agent}, follow_redirects=True
        )
        if check_robots:
            self._assert_robots_allows(DDG_URL)

    def _assert_robots_allows(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            resp = self._client.get(robots_url)
        except httpx.HTTPError:
            return
        if resp.status_code >= 400:
            return
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        if not rp.can_fetch(self.user_agent, url):
            raise WebsiteDiscoveryCircuitBreakerOpen(
                f"robots.txt disallows {url}"
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

    def _fetch(self, method: str, url: str, params: Optional[dict] = None) -> str:
        body = urllib.parse.urlencode(params or {}, doseq=True)
        key = _cache_key(method, url, body)
        cached = self._cache_read(key)
        if cached is not None:
            return cached

        self._throttle()

        attempts = 0
        while True:
            attempts += 1
            try:
                resp = self._client.get(url, params=params) if method == "GET" \
                    else self._client.post(url, data=params)
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

        search_html = self._fetch("GET", DDG_URL, params={"q": query})
        candidates = parse_ddg_results(search_html)

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

            # Verification signal 1: domain label contains name tokens.
            # 0.5 is enough when combined with title overlap ≥ 0.7 — the
            # combination is a very strong signal that this is the business.
            # Paniagua namesake at 0.0 still fails; Synergy at 2/3 passes.
            domain_overlap = _domain_name_overlap(name, host)
            if domain_overlap >= 0.5:
                reasons.append(f"domain_overlap={domain_overlap:.2f}")
                candidate = DiscoveryResult(
                    url=c["url"], confidence=title_overlap, reasons=reasons
                )
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
                continue

            # Verification signal 2: root homepage mentions city/zip/phone.
            # Verification signal 3: fallback to /contact, /about pages when
            # the root doesn't have the geo signal (common for service
            # businesses whose landing page is marketing copy).
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
        """Fetch root + a few common contact/about paths and look for any
        city / zip / phone co-mention. Returns (ok, reasons)."""
        parsed = urllib.parse.urlparse(root_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        urls_to_try = [root_url] + [base + p for p in _FALLBACK_PATHS]
        for url in urls_to_try:
            try:
                html = self._fetch("GET", url)
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
