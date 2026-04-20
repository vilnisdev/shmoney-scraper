from typing import Optional

import httpx

from .base import RawBusiness
from .license_base import LicenseAdapterBase


PORTAL_BASE = "https://opendata.howardcountymd.gov"
LIQUOR_LICENSE_RESOURCE = "/resource/tk3t-mn7e.json"


class HowardLicenseAdapter(LicenseAdapterBase):
    source_name = "howard-license"

    def __init__(
        self,
        resource_path: str = LIQUOR_LICENSE_RESOURCE,
        app_token: Optional[str] = None,
        **kwargs,
    ):
        self.resource_path = resource_path
        self.app_token = app_token
        super().__init__(**kwargs)
        if app_token:
            self._client.headers["X-App-Token"] = app_token

    def _build_default_client(self) -> httpx.Client:
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        return httpx.Client(base_url=PORTAL_BASE, headers=headers, timeout=30.0)

    def _fetch_page(self, offset: int) -> tuple[list[dict], bool]:
        params = {"$limit": str(self.page_size), "$offset": str(offset)}
        resp = self._client.get(self.resource_path, params=params)
        resp.raise_for_status()
        records = resp.json()
        has_more = len(records) >= self.page_size
        return records, has_more

    def _to_raw_business(self, rec: dict) -> Optional[RawBusiness]:
        trade = (rec.get("trade_name") or "").strip()
        corp = (rec.get("corp_name") or "").strip()
        name = trade or corp
        if not name:
            return None
        return RawBusiness(
            source=self.source_name,
            name=name,
            address=(rec.get("address") or "").strip(),
            business_type=(rec.get("description") or "").strip() or None,
            raw=rec,
        )
