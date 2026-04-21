from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional

from .audit import WebsiteAuditor
from .canonicalize import canonical_key
from .classify import classify, consulting_opportunity_tag
from .repo import Repository
from .scorer import score
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


@dataclass
class RunSummary:
    source: str
    fetched: int = 0
    canonicalized: int = 0
    qualified_out: int = 0
    written: int = 0

    def __int__(self) -> int:
        return self.written


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


def build_business_row(
    merged: dict,
    classification,
    score_val: int,
    tag: Optional[str],
) -> dict[str, str]:
    business_row: dict[str, str] = {}
    for attr, col in _COL_MAP.items():
        val = merged.get(attr)
        if val is None:
            continue
        business_row[col] = str(val)
    yib = _years_in_business(merged.get("registered_at"))
    if yib is not None:
        business_row["Years in Business"] = yib
    co_tag = consulting_opportunity_tag(classification)
    if not co_tag and tag:
        co_tag = tag
    business_row["Consulting Opportunity"] = co_tag
    business_row["Online Presence\n(1-5 scale)"] = str(score_val)
    return business_row


def sheet_sync(repo: Repository, sheets: SheetsWriter) -> int:
    """Rebuild the Sheet from SQLite in priority order.

    Priority: rows with no website first (highest outreach value — target
    doesn't exist online yet), then by `Online Presence` score ascending
    (weakest real sites next, strongest last).
    """
    sheets.ensure_schema()
    scored: list[tuple[bool, int, str, dict, object, int, Optional[str]]] = []
    for key in repo.list_sheet_keys():
        merged = repo.get_business(key)
        if not merged:
            continue
        classification = classify(merged.get("website"))
        audit_report = repo.get_audit(key)
        s, tag = score(classification, audit_report)
        has_website = bool((merged.get("website") or "").strip())
        scored.append((has_website, s, key, merged, classification, s, tag))

    # False (no website) sorts before True; then score ascending.
    scored.sort(key=lambda t: (t[0], t[1]))

    sheets.reset_data_rows()
    count = 0
    for _, _, key, merged, classification, s, tag in scored:
        row_dict = build_business_row(merged, classification, s, tag)
        row_idx = sheets.upsert(key, row_dict)
        repo.record_sheet_row(key, row_idx)
        count += 1
    return count


def run_once(
    adapter: SourceAdapter,
    repo: Repository,
    sheets: SheetsWriter,
    auditor: Optional[WebsiteAuditor] = None,
    dry_run: bool = False,
    reaudit: bool = False,
) -> RunSummary:
    sheets.ensure_schema()
    summary = RunSummary(source=adapter.source_name)
    for raw in adapter.iter_businesses():
        summary.fetched += 1
        key = canonical_key(raw.name, raw.address)
        repo.record_raw(adapter.source_name, key, asdict(raw))
        repo.upsert_business(key, raw)
        summary.canonicalized += 1

        merged = repo.get_business(key) or {}
        classification = classify(merged.get("website"))

        audit_report = None
        if classification.kind == "real" and merged.get("website") and auditor is not None:
            if not reaudit and repo.is_audit_fresh(key, max_age_days=30):
                audit_report = repo.get_audit(key)
            else:
                audit_report = auditor.audit(merged["website"])
                repo.record_audit(key, audit_report)

        s, tag = score(classification, audit_report)

        if classification.kind == "real" and s >= 4:
            summary.qualified_out += 1
            continue

        business_row = build_business_row(merged, classification, s, tag)

        if dry_run:
            continue

        row_idx = sheets.upsert(key, business_row)
        repo.record_sheet_row(key, row_idx)
        summary.written += 1
    return summary
