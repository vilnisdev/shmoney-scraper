import time
from typing import Iterator, Optional

import httpx

from .base import RawBusiness, SourceAdapter


class MarylandSDATAdapter(SourceAdapter):
    source_name = "md-sdat"

    def __init__(
        self,
        resource_path: str,
        base_url: str = "https://opendata.maryland.gov",
        limit: int = 20,
        app_token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        throttle_seconds: float = 0.5,
    ):
        self.resource_path = resource_path
        self.base_url = base_url
        self.limit = limit
        self.app_token = app_token
        self.throttle_seconds = throttle_seconds
        if client is None:
            headers = {}
            if app_token:
                headers["X-App-Token"] = app_token
            client = httpx.Client(base_url=base_url, headers=headers, timeout=15.0)
        self._client = client

    def iter_businesses(self) -> Iterator[RawBusiness]:
        page_size = min(self.limit, 1000)
        offset = 0
        emitted = 0
        while emitted < self.limit:
            resp = self._client.get(
                self.resource_path,
                params={"$limit": str(page_size), "$offset": str(offset)},
            )
            resp.raise_for_status()
            records = resp.json()
            if not records:
                return
            for rec in records:
                if emitted >= self.limit:
                    return
                yield self._to_raw(rec)
                emitted += 1
            if len(records) < page_size:
                return
            offset += page_size
            time.sleep(self.throttle_seconds)

    @staticmethod
    def _to_raw(rec: dict) -> RawBusiness:
        name = rec.get("entity_name") or rec.get("name") or ""
        address = (
            rec.get("principal_office")
            or rec.get("address")
            or rec.get("principal_office_address")
            or ""
        )
        owner = (
            rec.get("resident_agent_name")
            or rec.get("owner_name")
            or rec.get("principal_name")
        )
        registered = rec.get("formation_date") or rec.get("registration_date")
        return RawBusiness(
            source="md-sdat",
            name=name,
            address=address,
            owner_name=owner,
            registered_at=registered,
            raw=rec,
        )
