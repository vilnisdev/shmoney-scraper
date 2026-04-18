from dataclasses import asdict
from datetime import date
from typing import Optional

from .canonicalize import canonical_key
from .classify import classify, consulting_opportunity_tag
from .repo import Repository
from .sheets import SheetsWriter
from .sources.base import SourceAdapter


_COL_MAP: dict[str, str] = {
    "name": "Business Name",
    "owner_name": "Owner Name",
    "phone": "Phone Number",
    "address": "Address",
    "neighborhood": "Neighborhood / Area",
    "business_type": "Business Type",
    "source": "Source Found",
    "website": "Website URL\n(if any)",
    "yelp_or_google_listing": "Yelp / Google Listing?",
    "review_count": "# of Reviews",
}


def _years_in_business(registered_at: Optional[str]) -> Optional[str]:
    if not registered_at:
        return None
    try:
        year = int(registered_at[:4])
    except ValueError:
        return None
    now = date.today().year
    diff = now - year
    return str(diff) if diff >= 0 else None


def run_once(adapter: SourceAdapter, repo: Repository, sheets: SheetsWriter) -> int:
    sheets.ensure_schema()
    written = 0
    for raw in adapter.iter_businesses():
        key = canonical_key(raw.name, raw.address)
        repo.record_raw(adapter.source_name, key, asdict(raw))
        repo.upsert_business(key, raw)

        merged = repo.get_business(key) or {}

        classification = classify(merged.get("website"))
        if classification.kind == "real":
            continue

        business_row: dict[str, str] = {}
        for attr, col in _COL_MAP.items():
            val = merged.get(attr)
            if val is None:
                continue
            business_row[col] = str(val)
        yib = _years_in_business(merged.get("registered_at"))
        if yib is not None:
            business_row["Years in Business"] = yib
        business_row["Consulting Opportunity"] = consulting_opportunity_tag(classification)

        row_idx = sheets.upsert(key, business_row)
        repo.record_sheet_row(key, row_idx)
        written += 1
    return written
