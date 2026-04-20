"""Maryland SDAT Business Express enrichment scraper.

Enriches `businesses.owner_name` / `registered_at` for rows we already have.
Not a SourceAdapter — it doesn't iterate remote pages. Operator points the
CLI's `enrich` subcommand at this.

Ban-avoidance is the primary design constraint. See docs/issue-27 or the plan
file for the full rationale.
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
from typing import Callable, Iterable, Optional

import httpx
from bs4 import BeautifulSoup


SEARCH_URL = "https://egov.maryland.gov/BusinessExpress/EntitySearch"
ROBOTS_URL = "https://egov.maryland.gov/robots.txt"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 (+shmoney-scraper; contact: operator)"
)

_CHALLENGE_MARKERS = (
    "cloudflare",
    "captcha",
    "access denied",
    "attention required",
    "just a moment",
)

_DEAD_STATUSES = {"forfeited", "dissolved", "merged", "revoked"}


class SdatCircuitBreakerOpen(RuntimeError):
    """Raised when the scraper must abort (429, challenge page, repeated 5xx,
    robots disallow). The CLI catches this, saves watermark, exits 2."""


@dataclass
class SdatRecord:
    legal_name: str
    entity_type: str
    status: str
    department_id: str
    formation_date: Optional[str]
    principal_address: Optional[str]
    resident_agent: Optional[str]
    officers: list[str] = field(default_factory=list)


def pick_owner(rec: SdatRecord) -> Optional[str]:
    """Prefer first officer (corp). Fall back to resident agent (LLC).
    None if neither."""
    for name in rec.officers:
        if name and name.strip():
            return name.strip()
    if rec.resident_agent and rec.resident_agent.strip():
        return rec.resident_agent.strip()
    return None


def _cache_key(method: str, url: str, body: str) -> str:
    h = hashlib.sha1()
    h.update(method.encode())
    h.update(b"|")
    h.update(url.encode())
    h.update(b"|")
    h.update(body.encode())
    return h.hexdigest()


def _looks_like_challenge(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in _CHALLENGE_MARKERS)


def parse_search_results(html: str) -> list[dict]:
    """Parse the EntitySearch results page. Returns a list of candidate
    entities with fields used for match selection: dept_id, name, address,
    status, detail_href."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    # ASP.NET-style data-bound table. Rows live under a results table with
    # class or id containing "Results". Tolerate both patterns.
    table = soup.find("table", id=re.compile(r"[Rr]esults"))
    if table is None:
        table = soup.find("table", class_=re.compile(r"[Rr]esults"))
    if table is None:
        return []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td"])
        if len(cells) < 3:
            continue
        link = tr.find("a")
        out.append({
            "dept_id": cells[0].get_text(strip=True),
            "name": cells[1].get_text(strip=True) if len(cells) > 1 else "",
            "status": cells[2].get_text(strip=True) if len(cells) > 2 else "",
            "address": cells[3].get_text(strip=True) if len(cells) > 3 else "",
            "detail_href": link.get("href") if link else None,
        })
    return out


def parse_detail(html: str) -> Optional[SdatRecord]:
    """Parse a single-entity detail page. Returns None if the page doesn't
    look like an entity detail (e.g. blank stub)."""
    soup = BeautifulSoup(html, "html.parser")

    def field_value(label_rx: str) -> Optional[str]:
        label = soup.find(string=re.compile(label_rx, re.I))
        if not label:
            return None
        # ASP.NET detail pages use label-then-value patterns — walk the
        # next sibling cell/span.
        parent = label.parent
        sib = parent.find_next(["td", "span", "div"])
        if sib is None:
            return None
        val = sib.get_text(" ", strip=True)
        return val or None

    legal_name = field_value(r"^\s*(Department|Entity)\s*Name")
    dept_id = field_value(r"^\s*(Department|Business)\s*ID") or ""
    entity_type = field_value(r"Entity\s*Type") or ""
    status = field_value(r"Status") or ""
    formation_date = field_value(r"(Formation|Incorporation)\s*Date")
    principal = field_value(r"Principal\s*Office")
    resident_agent = field_value(r"Resident\s*Agent\s*Name")

    officers: list[str] = []
    officer_section = soup.find(string=re.compile(r"Officers", re.I))
    if officer_section:
        table = officer_section.find_parent().find_next("table")
        if table is not None:
            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if not cells:
                    continue
                # First cell name, tolerate title-column variants.
                name = cells[0].get_text(" ", strip=True)
                if name and name.lower() not in {"name", "officer"}:
                    officers.append(name)

    if not legal_name and not dept_id:
        return None

    return SdatRecord(
        legal_name=legal_name or "",
        entity_type=entity_type,
        status=status,
        department_id=dept_id,
        formation_date=formation_date,
        principal_address=principal,
        resident_agent=resident_agent,
        officers=officers,
    )


def _score_candidate(cand: dict, address_hint: Optional[str]) -> int:
    score = 0
    status_low = (cand.get("status") or "").lower()
    if "active" in status_low:
        score += 10
    if any(d in status_low for d in _DEAD_STATUSES):
        score -= 20
    if address_hint:
        addr_low = (cand.get("address") or "").lower()
        # Zip prefix overlap + street-number overlap are cheap proxies for
        # same-business vs namesake.
        zip_match = re.search(r"\b\d{5}\b", addr_low)
        hint_zip = re.search(r"\b\d{5}\b", address_hint.lower())
        if zip_match and hint_zip and zip_match.group() == hint_zip.group():
            score += 5
        num_match = re.search(r"^\s*(\d+)", addr_low)
        hint_num = re.search(r"^\s*(\d+)", address_hint.lower())
        if num_match and hint_num and num_match.group(1) == hint_num.group(1):
            score += 5
    return score


class SdatEnricher:
    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        min_interval_s: float = 4.0,
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
            self._assert_robots_allows()

    def _assert_robots_allows(self) -> None:
        try:
            resp = self._client.get(ROBOTS_URL)
        except httpx.HTTPError:
            # No robots served — not a disallow.
            return
        if resp.status_code >= 400:
            return
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        if not rp.can_fetch(self.user_agent, SEARCH_URL):
            raise SdatCircuitBreakerOpen("robots.txt disallows EntitySearch")

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
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def _cache_write(self, key: str, text: str) -> None:
        if not self._cache_dir:
            return
        p = self._cache_dir / f"{key}.html"
        p.write_text(text, encoding="utf-8")

    def _fetch(self, method: str, url: str, data: Optional[dict] = None) -> str:
        body = urllib.parse.urlencode(data or {}, doseq=True)
        key = _cache_key(method, url, body)
        cached = self._cache_read(key)
        if cached is not None:
            return cached

        self._throttle()

        attempts = 0
        while True:
            attempts += 1
            try:
                if method == "GET":
                    resp = self._client.get(url)
                else:
                    resp = self._client.post(url, data=data)
            except httpx.HTTPError as e:
                if attempts >= 2:
                    raise SdatCircuitBreakerOpen(f"network error: {e}") from e
                self._sleep(10.0)
                continue
            finally:
                self._last_fetch_at = self._now()

            status = resp.status_code
            text = resp.text

            if status == 429:
                raise SdatCircuitBreakerOpen(
                    f"429 received; retry-after={resp.headers.get('Retry-After')}"
                )
            if 500 <= status < 600:
                if attempts >= 2:
                    raise SdatCircuitBreakerOpen(f"{status} after retry")
                self._sleep(10.0)
                continue
            if status >= 400:
                raise SdatCircuitBreakerOpen(f"unexpected status {status}")
            if _looks_like_challenge(text):
                raise SdatCircuitBreakerOpen("challenge page detected")

            self._cache_write(key, text)
            return text

    def enrich(
        self, name: str, address_hint: Optional[str] = None
    ) -> Optional[SdatRecord]:
        search_html = self._fetch(
            "POST", SEARCH_URL, data={"searchType": "Entity", "searchValue": name}
        )
        candidates = parse_search_results(search_html)
        if not candidates:
            return None

        ranked = sorted(
            candidates,
            key=lambda c: _score_candidate(c, address_hint),
            reverse=True,
        )
        best = ranked[0]
        status_low = (best.get("status") or "").lower()
        if any(d in status_low for d in _DEAD_STATUSES):
            return None

        href = best.get("detail_href")
        if not href:
            return None
        detail_url = urllib.parse.urljoin(SEARCH_URL, href)
        detail_html = self._fetch("GET", detail_url)
        rec = parse_detail(detail_html)
        if rec is None:
            return None
        status_low = (rec.status or "").lower()
        if any(d in status_low for d in _DEAD_STATUSES):
            return None
        return rec

    def close(self) -> None:
        self._client.close()


def iter_enrichment_candidates(
    rows: Iterable[tuple[str, str, str]],
) -> Iterable[tuple[str, str, str]]:
    """Pass-through iterator so the CLI can depend on an abstraction that
    future adapters (property records, etc.) will share."""
    yield from rows
