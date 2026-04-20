import time
from typing import Callable, Iterator, Optional

import httpx

from .base import RawBusiness, SourceAdapter


FEATURE_SERVICE_BASE = "https://services1.arcgis.com"
QUERY_PATH = (
    "/UWYHeuuJISiGmgXx/arcgis/rest/services/MBWOO_Geocoded/FeatureServer/0/query"
)


def _compose_address(a: dict) -> str:
    street = (a.get("user_streetaddress") or "").strip()
    city = (a.get("user_city") or "").strip()
    state = (a.get("user_state") or "").strip()
    zipc = (a.get("user_zipcode") or "").strip().rstrip("-")
    tail = " ".join(p for p in [state, zipc] if p)
    parts = [street, city, tail]
    return ", ".join(p for p in parts if p)


def _compose_owner(a: dict) -> Optional[str]:
    first = (a.get("user_firstname") or "").strip()
    last = (a.get("user_lastname") or "").strip()
    name = " ".join(p for p in [first, last] if p)
    return name or None


def _clean_website(value) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip()
    if not v or v.upper() == "NULL":
        return None
    return v


class BaltimoreCityLicenseAdapter(SourceAdapter):
    source_name = "baltimore-city-license"

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        page_size: int = 1000,
        limit: Optional[int] = None,
        min_interval_s: float = 0.5,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.page_size = page_size
        self.limit = limit
        self.min_interval_s = min_interval_s
        self._now = now
        self._sleep = sleep
        if client is None:
            client = httpx.Client(base_url=FEATURE_SERVICE_BASE, timeout=30.0)
        self._client = client

    def iter_businesses(self) -> Iterator[RawBusiness]:
        emitted = 0
        offset = 0
        last_fetch_at: Optional[float] = None

        while True:
            if self.limit is not None and emitted >= self.limit:
                return

            if last_fetch_at is not None:
                elapsed = self._now() - last_fetch_at
                wait = self.min_interval_s - elapsed
                if wait > 0:
                    self._sleep(wait)

            params = {
                "where": "1=1",
                "outFields": "*",
                "f": "json",
                "resultOffset": str(offset),
                "resultRecordCount": str(self.page_size),
            }
            resp = self._client.get(QUERY_PATH, params=params)
            last_fetch_at = self._now()
            resp.raise_for_status()
            payload = resp.json()

            features = payload.get("features", [])
            if not features:
                return

            for feat in features:
                if self.limit is not None and emitted >= self.limit:
                    return
                a = feat.get("attributes", {})
                if (a.get("user_contractstatus") or "").strip().upper() != "CERTIFY":
                    continue
                yield RawBusiness(
                    source=self.source_name,
                    name=(a.get("user_company") or "").strip(),
                    address=_compose_address(a),
                    phone=a.get("user_phone") or None,
                    website=_clean_website(a.get("user_website")),
                    business_type=a.get("user_category") or None,
                    owner_name=_compose_owner(a),
                    registered_at=a.get("user_origcert") or None,
                    raw=a,
                )
                emitted += 1

            if not payload.get("exceededTransferLimit"):
                return
            offset += len(features)
