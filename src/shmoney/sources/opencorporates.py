import time
from typing import Iterator, Optional

import httpx

from .base import RawBusiness, SourceAdapter


class OpenCorporatesMDAdapter(SourceAdapter):
    source_name = "md-sdat"

    def __init__(
        self,
        api_token: str,
        jurisdiction_code: str = "us_md",
        query: str = "",
        limit: int = 20,
        fetch_officers: bool = True,
        client: Optional[httpx.Client] = None,
        throttle_seconds: float = 0.5,
    ):
        self.api_token = api_token
        self.jurisdiction_code = jurisdiction_code
        self.query = query
        self.limit = limit
        self.fetch_officers = fetch_officers
        self.throttle_seconds = throttle_seconds
        if client is None:
            client = httpx.Client(
                base_url="https://api.opencorporates.com", timeout=15.0
            )
        self._client = client

    def iter_businesses(self) -> Iterator[RawBusiness]:
        emitted = 0
        page = 1
        while emitted < self.limit:
            params = {
                "q": self.query,
                "jurisdiction_code": self.jurisdiction_code,
                "per_page": str(min(self.limit - emitted, 100)),
                "page": str(page),
                "api_token": self.api_token,
            }
            resp = self._client.get("/v0.4/companies/search", params=params)
            resp.raise_for_status()
            data = resp.json().get("results", {})
            companies = data.get("companies", [])
            if not companies:
                return
            for wrapper in companies:
                if emitted >= self.limit:
                    return
                company = wrapper.get("company", wrapper)
                owner = None
                if self.fetch_officers:
                    time.sleep(self.throttle_seconds)
                    owner = self._fetch_primary_officer(company)
                yield RawBusiness(
                    source="md-sdat",
                    name=company.get("name", ""),
                    address=company.get("registered_address_in_full") or "",
                    owner_name=owner,
                    registered_at=company.get("incorporation_date"),
                    raw=company,
                )
                emitted += 1
            total_pages = data.get("total_pages") or page
            if page >= total_pages:
                return
            page += 1
            time.sleep(self.throttle_seconds)

    def _fetch_primary_officer(self, company: dict) -> Optional[str]:
        number = company.get("company_number")
        juris = company.get("jurisdiction_code") or self.jurisdiction_code
        if not number:
            return None
        resp = self._client.get(
            f"/v0.4/companies/{juris}/{number}",
            params={"api_token": self.api_token},
        )
        if resp.status_code != 200:
            return None
        detail = resp.json().get("results", {}).get("company", {})
        officers = detail.get("officers") or []
        for entry in officers:
            officer = entry.get("officer", entry)
            if officer.get("inactive"):
                continue
            name = officer.get("name")
            if name:
                return name
        return None
