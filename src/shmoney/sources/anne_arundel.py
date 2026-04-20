from typing import Optional

import httpx

from .base import RawBusiness
from .license_base import LicenseAdapterBase


FEATURE_SERVICE_BASE = "https://gis.aacounty.org"
QUERY_PATH = (
    "/arcgis/rest/services/OpenData/Planning_aacoPZProd_OpenData/FeatureServer/9/query"
)


def _compose_address(a: dict) -> str:
    street = (a.get("user_street_address") or "").strip()
    city = (a.get("user_city") or "").strip()
    state = (a.get("user_st") or "").strip()
    zipc = (a.get("user_zip") or "").strip().split("-")[0]
    tail = " ".join(p for p in [state, zipc] if p)
    return ", ".join(p for p in [street, city, tail] if p)


class AnneArundelLicenseAdapter(LicenseAdapterBase):
    """Anne Arundel County liquor license locations (ArcGIS feature service).

    The feed carries only the licensed trade (DBA), address, and license
    class. No human owner, no phone, no dates. `owner_name` is left None
    so a later canonical_key join with OpenCorporates or Baltimore City
    MBE can fill it in.
    """

    source_name = "anne-arundel-license"

    def _build_default_client(self) -> httpx.Client:
        return httpx.Client(base_url=FEATURE_SERVICE_BASE, timeout=30.0)

    def _fetch_page(self, offset: int) -> tuple[list[dict], bool]:
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": str(self.page_size),
        }
        resp = self._client.get(QUERY_PATH, params=params)
        resp.raise_for_status()
        payload = resp.json()
        records = [f.get("attributes", {}) for f in payload.get("features", [])]
        return records, bool(payload.get("exceededTransferLimit"))

    def _to_raw_business(self, a: dict) -> Optional[RawBusiness]:
        name = (a.get("user_trade_name") or "").strip()
        if not name:
            return None
        return RawBusiness(
            source=self.source_name,
            name=name,
            address=_compose_address(a),
            business_type=(a.get("class") or "").strip() or None,
            raw=a,
        )
