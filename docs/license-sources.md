# License data sources per jurisdiction (issue #7 research)

Status of bulk/API access to business-license data for each jurisdiction in
issue #7's scope. Use this to decide, per jurisdiction, whether to build an
adapter now, defer, or fall back to a shared registry.

| Jurisdiction | Portal | Platform | Business-license dataset | Status |
| --- | --- | --- | --- | --- |
| Baltimore City | `data.baltimorecity.gov` | ArcGIS FeatureServer | MBE/WBE certifications (`MBWOO_Geocoded/0`) | **built** — `--source baltimore-city` |
| Baltimore County | `opendata.baltimorecountymd.gov` | ArcGIS Hub | only "Rental License" (landlord-focused, not small-business leads); no business-license dataset | **deferred** |
| Anne Arundel | `gis.aacounty.org` | ArcGIS FeatureServer | Liquor License Location (`Planning_aacoPZProd_OpenData/9`) | **built** — `--source anne-arundel` |
| Howard | `opendata.howardcountymd.gov` | Socrata SODA | Liquor Licenses (`tk3t-mn7e`) | **built** — `--source howard` |
| Harford | none | — | no open-data portal; MD Judiciary search UI only | **deferred** |
| Carroll | none | — | no open-data portal; MD Judiciary search UI or FOIA only | **deferred** |

## Cross-cutting: Maryland Judiciary Business Licenses Online

`jportal.mdcourts.gov/license/` indexes business licenses issued by the Clerk
of the Circuit Court in every Maryland jurisdiction — including Harford and
Carroll. But:

- interactive search only, gated by a disclaimer page,
- no documented API,
- no bulk export.

Treated the same way as Facebook pages in issue #6: **do not scrape around the
gate**. If a future adapter uses this source it must go through a sanctioned
access path (FOIA request, MPIA request, or direct partnership with AOC).

## How #4 relates

Issue #7 is marked blocked by #4 (Maryland SDAT). #4 was deferred because the
state-level SDAT endpoint is not available as a bulk data source. The
*license-to-business join pattern* the issue was meant to establish landed
via PR #12 (`OpenCorporatesMDAdapter`). The join itself is just the COALESCE
upsert at `src/shmoney/repo.py::upsert_business` — every license adapter
reuses it by populating `owner_name` / `registered_at` on `RawBusiness`.

#7 proceeds without #4.

## Delivery history

1. **Phase A** (PR #16): endpoint research + this document.
2. **Phase B** (PR #17): `BaltimoreCityLicenseAdapter`.
3. **Phase C** (PR #18): `HowardLicenseAdapter` + extracted `LicenseAdapterBase`.
4. **Phase D** (this PR): `AnneArundelLicenseAdapter`; Baltimore County deferred (only feed is Rental License, not relevant to SMB lead-gen); `BaltimoreCityLicenseAdapter` refactored onto `LicenseAdapterBase`.

Deferred jurisdictions (`baltimore-county`, `harford`, `carroll`) all raise a loud `BadParameter` pointing here when invoked via `pipeline run --source <name>` — no silent 0-row runs.
