import time
from abc import abstractmethod
from typing import Callable, Iterator, Optional

import httpx

from .base import RawBusiness, SourceAdapter


class LicenseAdapterBase(SourceAdapter):
    """Shared scaffold for paginated license-data adapters.

    Subclasses override `_fetch_page(offset)` to return `(records, has_more)`
    and `_to_raw_business(record)` to map one record (or skip by returning
    None). The base owns the iteration loop, the `limit` cap, and the
    per-request rate limiter. `now` and `sleep` are injectable so tests can
    assert sleep durations without real clock waits.
    """

    default_page_size: int = 1000

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        page_size: Optional[int] = None,
        limit: Optional[int] = None,
        min_interval_s: float = 0.5,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start_offset: int = 0,
        checkpoint: Optional[Callable[[int], None]] = None,
    ):
        self.page_size = page_size if page_size is not None else self.default_page_size
        self.limit = limit
        self.min_interval_s = min_interval_s
        self._now = now
        self._sleep = sleep
        self._start_offset = start_offset
        self._checkpoint = checkpoint
        self._client = client if client is not None else self._build_default_client()

    @abstractmethod
    def _build_default_client(self) -> httpx.Client:
        raise NotImplementedError

    @abstractmethod
    def _fetch_page(self, offset: int) -> tuple[list[dict], bool]:
        """Return (records, has_more). Empty records ends iteration."""
        raise NotImplementedError

    @abstractmethod
    def _to_raw_business(self, record: dict) -> Optional[RawBusiness]:
        """Map one record. Return None to skip (filtered out)."""
        raise NotImplementedError

    def iter_businesses(self) -> Iterator[RawBusiness]:
        emitted = 0
        offset = self._start_offset
        last_fetch_at: Optional[float] = None

        while True:
            if self.limit is not None and emitted >= self.limit:
                return

            if last_fetch_at is not None:
                wait = self.min_interval_s - (self._now() - last_fetch_at)
                if wait > 0:
                    self._sleep(wait)

            records, has_more = self._fetch_page(offset)
            last_fetch_at = self._now()

            if not records:
                return

            for rec in records:
                if self.limit is not None and emitted >= self.limit:
                    return
                raw = self._to_raw_business(rec)
                if raw is None:
                    continue
                yield raw
                emitted += 1

            offset += len(records)
            if self._checkpoint is not None:
                self._checkpoint(offset)

            if not has_more:
                return
