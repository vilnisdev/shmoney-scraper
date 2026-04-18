from typing import Iterator, Optional

import httpx

from .base import RawBusiness, SourceAdapter


class YelpFusionAdapter(SourceAdapter):
    source_name = "yelp"

    def __init__(
        self,
        api_key: str,
        location: str = "Baltimore, MD",
        term: str = "restaurants",
        limit: int = 20,
        client: Optional[httpx.Client] = None,
    ):
        self.api_key = api_key
        self.location = location
        self.term = term
        self.limit = limit
        if client is None:
            client = httpx.Client(
                base_url="https://api.yelp.com/v3",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
        self._client = client

    def iter_businesses(self) -> Iterator[RawBusiness]:
        resp = self._client.get(
            "/businesses/search",
            params={"location": self.location, "term": self.term, "limit": self.limit},
        )
        resp.raise_for_status()
        for biz in resp.json().get("businesses", []):
            yield RawBusiness(
                source="yelp",
                name=biz.get("name", ""),
                address=" ".join(biz.get("location", {}).get("display_address", [])),
                phone=biz.get("display_phone") or biz.get("phone") or None,
                website=None,
                neighborhood=", ".join(biz.get("location", {}).get("neighborhoods", []) or []) or None,
                business_type=", ".join(c.get("title", "") for c in biz.get("categories", [])) or None,
                yelp_or_google_listing=biz.get("url") or "Yelp",
                review_count=biz.get("review_count"),
                raw=biz,
            )
