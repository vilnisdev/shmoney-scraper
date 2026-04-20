# License data sources per jurisdiction (issue #7 research)

Status of bulk/API access to business-license data for each jurisdiction in
issue #7's scope. Use this to decide, per jurisdiction, whether to build an
adapter now, defer, or fall back to a shared registry.

| Jurisdiction | Portal | Platform | Business-license dataset | Status |
| --- | --- | --- | --- | --- |
| Baltimore City | `data.baltimorecity.gov` | Socrata | yes (search "business license" on the catalog — dataset ID to be confirmed during adapter build) | **build** |
| Baltimore County | `opendata.baltimorecountymd.gov` | ArcGIS Hub | not confirmed — portal exists but licenses dataset presence is unverified | **verify, then build or defer** |
| Anne Arundel | `opendata.aacounty.org` (ArcGIS) + `aacounty.org` license-search UI | ArcGIS / HTML | portal lists trade/amusement/bingo licenses via HTML search; no confirmed bulk dataset | **verify, likely HTML scrape** |
| Howard | `opendata.howardcountymd.gov` | Socrata | partial: active electric/utility contractors, solicitors/peddlers, liquor licenses (2020 snapshot). No general business-license dataset. | **build (narrow: liquor + contractors)** |
| Harford | none | — | no open-data portal; records only via MD Judiciary search UI or in-person | **defer (no public bulk feed)** |
| Carroll | none | — | no open-data portal; records only via MD Judiciary search UI or FOIA | **defer (no public bulk feed)** |

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

## Delivery plan

1. **Phase B** (follow-up PR): Baltimore City adapter against
   `data.baltimorecity.gov` Socrata API. Confirm dataset ID during build.
2. **Phase C** (follow-up PR): Howard County adapter (liquor + contractors
   datasets). Extract `LicenseAdapterBase` after the second adapter lands.
3. **Phase D**: Baltimore County and Anne Arundel — build if the verification
   step above turns up a real bulk dataset; otherwise defer alongside Harford
   and Carroll.
4. **Deferred jurisdictions** (Harford, Carroll, and anything that fails
   verification): the CLI will expose `--source <name>` for them, and it must
   raise a loud `BadParameter` pointing here. No silent 0-row runs.

## Not in scope for this PR

Adapter code. This PR only records the research so the follow-up PRs can each
target one confirmed endpoint.
