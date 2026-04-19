from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
import time
from typing import Optional

import httpx


@dataclass(frozen=True)
class AuditFlags:
    reachable: bool
    https: bool
    redirects_to_https: bool
    has_viewport: bool
    body_substantial: bool
    response_time_ok: bool
    last_modified_fresh: bool


@dataclass(frozen=True)
class AuditReport:
    flags: AuditFlags
    fetched_at: str
    status_code: int
    elapsed_ms: int


_VIEWPORT_RE = re.compile(r"<meta[^>]+name=['\"]viewport['\"]", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BODY_MIN_CHARS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_scheme(url: str) -> str:
    return url if "://" in url else "https://" + url


def _strip_text(html: str) -> str:
    return _TAG_RE.sub(" ", html)


def _last_modified_fresh(header: Optional[str], stale_days: int) -> bool:
    # Absent header: don't penalize; many healthy sites omit it.
    if not header:
        return True
    try:
        dt = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt <= timedelta(days=stale_days)


class WebsiteAuditor:
    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        timeout: float = 10.0,
        response_ms_threshold: int = 3000,
        stale_days: int = 365,
    ):
        self._client = client or httpx.Client(follow_redirects=True, timeout=timeout)
        self._owns_client = client is None
        self._response_ms_threshold = response_ms_threshold
        self._stale_days = stale_days

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def audit(self, url: str) -> AuditReport:
        original = _ensure_scheme(url)
        original_scheme = original.split("://", 1)[0].lower()
        t0 = time.perf_counter()
        try:
            resp = self._client.get(original)
        except httpx.HTTPError:
            return AuditReport(
                flags=AuditFlags(
                    reachable=False,
                    https=False,
                    redirects_to_https=False,
                    has_viewport=False,
                    body_substantial=False,
                    response_time_ok=False,
                    last_modified_fresh=False,
                ),
                fetched_at=_now_iso(),
                status_code=0,
                elapsed_ms=0,
            )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        reachable = 200 <= resp.status_code < 300
        final_scheme = resp.url.scheme.lower()
        is_https = final_scheme == "https"
        redirects_to_https = original_scheme == "http" and is_https

        body = resp.text if reachable else ""
        has_viewport = bool(_VIEWPORT_RE.search(body))
        body_substantial = len(_strip_text(body).strip()) >= _BODY_MIN_CHARS

        flags = AuditFlags(
            reachable=reachable,
            https=is_https if reachable else False,
            redirects_to_https=redirects_to_https if reachable else False,
            has_viewport=has_viewport,
            body_substantial=body_substantial,
            response_time_ok=reachable and elapsed_ms < self._response_ms_threshold,
            last_modified_fresh=reachable
            and _last_modified_fresh(resp.headers.get("last-modified"), self._stale_days),
        )
        return AuditReport(
            flags=flags,
            fetched_at=_now_iso(),
            status_code=resp.status_code,
            elapsed_ms=elapsed_ms,
        )
