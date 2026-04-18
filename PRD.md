# PRD — Baltimore Business Lead Pipeline

## Context

Operator wants to sell websites to Baltimore-area small businesses. Finding prospects by hand is slow. A pipeline that automatically surfaces local businesses that lack a real website (or have a bad one), enriches them with public-record data, and lands qualified rows in a Google Sheet turns prospecting from hours-per-lead into a one-shot CLI invocation. Output Sheet mirrors the operator's existing lead-tracking columns so outreach and follow-up workflow stays unchanged.

Seed research for sources lives in `baltimore_business_sources.json` at repo root. This PRD scopes the v1 pipeline to Tier-1 sources the operator chose: Yelp Fusion API, Maryland state + six jurisdiction business-license datasets, and Facebook Pages — geographic scope is the full metro (Baltimore City plus Baltimore, Anne Arundel, Howard, Harford, Carroll counties). All verticals. Pragmatic scraping posture (public HTML + APIs, respect robots.txt, rate-limit, no anti-bot evasion).

## Problem Statement

As a solo consultant selling websites to Baltimore-area small businesses, I spend hours hand-searching Google, Yelp, and Facebook trying to find businesses that either have no website or have a visibly bad one. Once I find a candidate I then hand-look up their license filing, phone number, address, and owner name across multiple government portals. The result is a few dozen leads per week instead of the hundreds I need to sustain outbound. I already track prospects in a Google Sheet with a fixed column layout; I don't want to change that workflow — I want the top of the funnel filled for me.

## Solution

A one-shot command-line pipeline that:

1. Pulls businesses from Yelp Fusion API, scraped Facebook Pages, and six Maryland/Baltimore public business-license / registration datasets.
2. Canonicalizes and deduplicates records across sources.
3. Classifies each business's web presence: no website, social-media-only, or real website.
4. For real websites, runs an automated quality audit and scores them 1–5.
5. Keeps only businesses that score poorly or have no real website.
6. Joins state/county license data onto the surviving candidates to fill owner name, address, phone, registration date (→ years in business).
7. Upserts survivors into the operator's existing Google Sheet by a stable dedup key, preserving any hand-edited columns.
8. Persists all state in a local SQLite database so re-runs are fast, resumable, and idempotent.

The operator runs one command, waits, and opens the Sheet to new qualified rows.

## User Stories

1. As a consultant, I want to run one CLI command and have new qualified leads appear in my Google Sheet, so that prospecting no longer requires manual browser work.
2. As a consultant, I want the Sheet's existing column layout preserved, so that my downstream outreach, follow-up, and outcome-tracking workflow stays unchanged.
3. As a consultant, I want hand-edited columns (Contact Status, Follow-Up Date, Outcome / Next Step, Pain Points / Notes) to never be overwritten by a re-run, so that my sales notes are safe.
4. As a consultant, I want the pipeline to skip any business it has already written to the Sheet, so that I never see the same lead twice.
5. As a consultant, I want every run to be resumable, so that a transient network failure or rate-limit doesn't force me to restart from scratch.
6. As a consultant, I want to run the pipeline one source at a time during debugging, so that I can iterate on a single adapter without paying the full-run cost.
7. As a consultant, I want Yelp-sourced businesses scored by review count and rating, so that I prefer established businesses with real customer bases over brand-new listings.
8. As a consultant, I want businesses whose "website" on Yelp/Facebook is actually a facebook.com, instagram.com, linktr.ee, or beacons.ai URL to be flagged as social-only, so that I correctly identify them as prospects.
9. As a consultant, I want websites that fail to load, fail HTTPS, or render without a mobile viewport to be flagged as low quality, so that obviously bad sites are surfaced as consulting opportunities.
10. As a consultant, I want each candidate's site given a 1–5 Online Presence score by a deterministic rule, so that I can sort the Sheet and work the worst sites first.
11. As a consultant, I want Maryland SDAT and county business-license data joined onto each candidate, so that Owner Name, Address, and Years in Business are filled automatically.
12. As a consultant, I want the pipeline to respect source rate limits and robots.txt, so that I don't get API keys revoked or IPs blocked.
13. As a consultant, I want all raw fetched data cached locally in SQLite, so that re-audits and schema changes don't require re-hitting the source APIs.
14. As a consultant, I want a CLI subcommand to re-audit only the website-scoring step, so that if I tune scoring rules I don't have to re-fetch anything.
15. As a consultant, I want a `--dry-run` flag, so that I can see what would be written to the Sheet before any rows actually change.
16. As a consultant, I want a `--limit N` flag, so that I can test the full pipeline on a small sample before a full metro sweep.
17. As a consultant, I want sensible log output showing per-source counts and per-stage filter dropoffs, so that I can tell where leads are being lost in the funnel.
18. As a consultant, I want API keys and the Google service-account JSON read from environment variables or a `.env` file, so that secrets never land in git.
19. As a consultant, I want businesses that already have a good website excluded, so that I'm not wasting attention on non-prospects.
20. As a consultant, I want businesses with revenue-estimate and pain-point fields left blank rather than guessed, so that I trust what is in the Sheet.
21. As a consultant, I want the Consulting Opportunity column populated with a short rule-based tag (e.g. "no website", "social-only", "site score 2/5"), so that I can skim and prioritize.
22. As a consultant, I want each source adapter to be independently runnable, so that I can add a new source later without re-architecting the pipeline.
23. As a consultant, I want the pipeline to write a stable unique key (e.g. normalized name + normalized address hash) into a hidden column on the Sheet, so that re-runs reliably upsert the same row.
24. As a consultant, I want a single `pipeline init` command that sets up the SQLite DB, creates Sheet columns if missing, and validates all credentials, so that first-time setup is one command.
25. As a consultant, I want to see the source(s) each lead came from, so that I know how to attribute inbound channel performance.

## Implementation Decisions

### Stack

- **Language:** Python 3.11+. Chosen for breadth of scraping ecosystem (`httpx`, `playwright`, `selectolax`/`beautifulsoup4`), mature Google Sheets client (`gspread`), stdlib SQLite, light CLI scaffolding (`typer`).
- **Persistence:** SQLite (single file under `./data/pipeline.db`) for all intermediate state: raw source fetches, canonical business records, audit results, Sheet-upsert watermarks, per-source resume cursors.
- **Secrets:** `.env` file, loaded via `python-dotenv`. Required vars: `YELP_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_SA_JSON_PATH`, optional `FACEBOOK_*` if Graph API path added later.
- **Output:** Single Google Sheet (ID from env) written via `gspread` + service-account auth.
- **CLI:** `typer`-based. Subcommands: `init`, `run`, `run --source <name>`, `reaudit`, `sheet-sync`, `reset`.

### Modules (deep, testable in isolation)

1. **`Canonicalizer`** — pure, stateless. Input: raw `(name, address, phone)`. Output: `CanonicalKey` (stable sha1) + normalized display fields. Handles LLC/Inc suffix strip, abbreviation expansion (St → Street, Ave → Avenue), phone to E.164, address case/spacing normalize. Deep module: small interface, lots of encapsulated rules, rarely changes.

2. **`WebsiteClassifier`** — pure, stateless. Input: raw URL (or null). Output: `{kind: "none"|"social"|"real", platform?: "facebook"|"instagram"|"linktree"|"beacons"|"other", url: str}`. Matches against a hardcoded host allowlist/blocklist.

3. **`WebsiteAuditor`** — side-effectful fetch, pure scoring. Input: URL. Output: `AuditReport {score: 1..5, flags: set[str], fetched_at: ts}`. Sub-checks: reachable, https, redirects to https, has `<meta viewport>`, response time under threshold, last-modified header freshness, page has more than placeholder amount of text. Flags contribute to score via a deterministic rule (documented in module). Uses `httpx` sync client with timeout. No JS render in v1 — cheaper, catches ~80%.

4. **`SourceAdapter` (abstract base) + concrete adapters**
   - `YelpFusionAdapter` — Yelp Fusion `/businesses/search` paginated by neighborhood+vertical grid; respects RPS limits.
   - `FacebookPagesAdapter` — public-HTML scraper over Page URLs discovered via seed searches; respects robots.txt and a single-threaded rate cap.
   - `MarylandSDATAdapter` — pulls recent entity filings from MD Business Express.
   - `BaltimoreCityLicenseAdapter`, `BaltimoreCountyLicenseAdapter`, `AnneArundelLicenseAdapter`, `HowardLicenseAdapter`, `HarfordLicenseAdapter`, `CarrollLicenseAdapter` — per-jurisdiction.
   - Common interface: `iter_businesses(watermark: Watermark) -> Iterator[RawBusiness]` and `source_name: str`. Each yields raw records; canonicalization happens downstream.
   - Adapters write raw payloads into `raw_fetches` table before yielding, so re-runs can rebuild canonical state without re-fetching.

5. **`Repository` (SQLite)** — owns schema + migrations. Tables: `raw_fetches`, `businesses` (canonical), `audits`, `source_watermarks`, `sheet_row_map`. Exposes: `upsert_business`, `record_audit`, `get_unaudited`, `get_watermark`, `set_watermark`, `next_pending_sheet_rows`. Repo is the only module that touches SQL.

6. **`SheetsWriter`** — owns Sheet column schema + upsert. Reads existing Sheet once per run, indexes by `CanonicalKey` (stored in a hidden last column), and either appends a new row or updates only the pipeline-owned columns on an existing row. Operator-owned columns (Contact Status, Follow-Up Date, Outcome / Next Step, Pain Points / Notes) are **never written** after initial creation. Column map is declared as a module constant so schema drift is a one-line change.

7. **`Scorer`** — pure rule: maps `(WebsiteClassifier result, AuditReport)` → `OnlinePresenceScore 1..5` + `ConsultingOpportunityTag` (short string). Also filters: score >= 4 means "not a prospect, skip Sheet write".

8. **`PipelineOrchestrator`** — glues the above. Phases:
   1. For each enabled adapter: resume from watermark, fetch, stash raw, canonicalize, upsert into `businesses`.
   2. Join license-source records onto Yelp/FB records by `CanonicalKey` to fill owner + registration date.
   3. For each business with a real website and stale/missing audit: run `WebsiteAuditor`, store `AuditReport`.
   4. For each business not yet in Sheet (or with a changed score): `Scorer` → filter → `SheetsWriter.upsert`.

### Sheet schema

Pipeline populates (on initial insert only, except where noted):

- Business Name, Owner Name, Phone Number, Address, Neighborhood / Area, Business Type, Source Found, Online Presence (1-5), Website URL (if any), Yelp / Google Listing?, # of Reviews, Estimated Annual Revenue (blank v1), Years in Business, Consulting Opportunity, Canonical Key (hidden, pipeline-only).

Pipeline never writes (operator-owned):

- Pain Points / Notes, Contact Status, Follow-Up Date, Outcome / Next Step.

### Qualification filter (who lands in the Sheet)

A business is written to the Sheet iff **any** of:
- `WebsiteClassifier.kind == "none"`, OR
- `WebsiteClassifier.kind == "social"`, OR
- `WebsiteClassifier.kind == "real"` AND `AuditReport.score <= 3`.

Score 4 or 5 → excluded.

### Resume / recovery

Each adapter has a row in `source_watermarks` (`source_name`, `cursor`, `last_run_ts`). Adapters checkpoint cursor after each successful page. A killed run resumes from the last checkpoint on next `pipeline run`. Audits are per-business — already-audited businesses are skipped unless `--reaudit` is passed or audit is older than 30 days.

### Rate limits + compliance

- Yelp: documented RPS respected via token bucket.
- Facebook: single-threaded, 1 req / 3s, respect robots.txt via `urllib.robotparser`, stop on any block signal.
- MD/county: 2 req/s default, configurable per adapter.
- User-Agent identifies the operator's project and contact.

## Testing Decisions

Good tests assert **external behavior** of each module — inputs and outputs, not private method calls or implementation internals. All four deep modules below get isolated unit tests with fixture inputs; the orchestrator gets a single end-to-end integration test with all network I/O mocked and a stub SQLite + a fake Sheets client.

### `Canonicalizer`

Table-driven unit tests covering:
- LLC/Inc/Co/Corp suffix stripping.
- Address abbreviation expansion and unit-number normalization.
- Phone number normalization across formats (`410-555-1234`, `(410) 555-1234`, `+14105551234`).
- Two inputs that should canonicalize to the same key do.
- Two inputs that look similar but are different businesses don't.

### `WebsiteClassifier`

Table-driven tests of URL → `{kind, platform}`:
- `null` / empty → none.
- `facebook.com/...`, `instagram.com/...`, `linktr.ee/...`, `beacons.ai/...` → social with platform.
- A real small-business URL → real.
- Edge cases: `m.facebook.com`, tracking-query-string URLs, trailing slashes.

### `WebsiteAuditor`

Fixture-based tests using `pytest-httpx` (or `responses`) to serve canned HTML + headers:
- Reachable + https + has viewport + fresh → score 4-5.
- Reachable + http only → score drops, flag set.
- 404 / timeout → score 1.
- Tiny placeholder "coming soon" page → score 1-2.
- Asserted on `AuditReport` shape, not internal fetch call counts.

### Source adapters

Each adapter gets fixture tests: record a real API/HTML response, check in under `tests/fixtures/`, assert the adapter parses it into the right `RawBusiness` shape. Protects against upstream format drift — when a source changes shape, exactly one test breaks and points at the adapter to update.

### `SheetsWriter`

Fake in-memory Sheet (list of dicts keyed by row index). Assert:
- New Canonical Key appends a new row with the right columns.
- Existing Canonical Key updates only the pipeline-owned columns.
- Operator-owned columns are never touched on re-run, even if their value differs.
- A Sheet missing expected columns fails `pipeline init` with a clear error.

### Prior art

None in this repo (new). The test patterns above are standard Python: `pytest` + `pytest-httpx` for HTTP mocking, `pytest` fixtures for fake DB/Sheet. No cross-module integration tests beyond the single orchestrator smoke test.

## Out of Scope (v1)

- Google Places API integration (paid; deferred per operator).
- LLM-generated pain points or outreach copy.
- Revenue estimation — `Estimated Annual Revenue` left blank.
- Vertical-specific directories (Angi, Healthgrades, StyleSeat, Avvo, etc.) — all verticals treated identically in v1.
- Cron / scheduled runs — one-shot CLI only, cron deferred.
- Multi-operator or cloud DB — SQLite local file only.
- Web UI — CLI only.
- Email / phone discovery via paid providers (Apollo, Hunter, ZoomInfo) — deferred.
- JS-rendered audits (Playwright) — `httpx` static fetch only in v1.
- Non-Baltimore geographies.

## Further Notes

- Per-source token budgets and fetch volumes should be logged and summarized at the end of a run so the operator can watch cost creep.
- The `sheet_row_map` table is the single source of truth for Sheet state; the Sheet itself is treated as downstream output, not source-of-truth. If the Sheet is deleted, re-running `pipeline sheet-sync` reconstructs it from SQLite.
- Facebook scraping is the most fragile adapter; expect to update selectors. Isolating it behind the `SourceAdapter` interface contains that churn.
- The 30-day audit re-check window is a guess; tune after first production run.

## Verification

End-to-end manual verification on a fresh checkout:

1. `git clone` repo, `python -m venv .venv`, `pip install -e .`.
2. `cp .env.example .env`, fill in Yelp key + path to Google service-account JSON + target Sheet ID.
3. `pipeline init` — creates SQLite DB, validates Yelp + Sheets creds, ensures Sheet has required columns (creates if missing).
4. `pipeline run --source yelp --limit 20 --dry-run` — hits Yelp Fusion, shows what would be written, writes nothing.
5. `pipeline run --source yelp --limit 20` — same, but writes up to 20 qualifying rows into the Sheet. Open Sheet, confirm rows have Business Name, Phone, Address, Neighborhood, Online Presence score, Website URL / social-only tag, Source = `yelp`.
6. Hand-edit Contact Status on one of the new rows. Re-run `pipeline run --source yelp --limit 20`. Confirm the hand-edited cell is preserved and no duplicate row was added.
7. `pipeline run --source md-sdat --limit 20` — confirm license data rows land and join onto the Yelp rows where they match (Owner Name and Years in Business populated on previously-created rows).
8. `pipeline run --source facebook --limit 10` — confirm social-only detection flags `facebook.com` URLs correctly and writes rows with `social-only` tag.
9. `pytest` — full test suite green.
10. `pipeline run` (no `--source`, no `--limit`) — full metro sweep. Spot-check the log summary for per-source counts and per-stage filter dropoffs.

## Critical files (to be created)

- `pyproject.toml` — deps, CLI entry point.
- `src/shmoney/canonicalize.py` — `Canonicalizer`.
- `src/shmoney/classify.py` — `WebsiteClassifier`.
- `src/shmoney/audit.py` — `WebsiteAuditor`.
- `src/shmoney/score.py` — `Scorer`.
- `src/shmoney/repo.py` — SQLite `Repository` + schema.
- `src/shmoney/sheets.py` — `SheetsWriter`.
- `src/shmoney/sources/base.py` — `SourceAdapter` ABC + `RawBusiness`.
- `src/shmoney/sources/yelp.py`, `.../facebook.py`, `.../md_sdat.py`, `.../baltimore_city.py`, `.../baltimore_county.py`, `.../anne_arundel.py`, `.../howard.py`, `.../harford.py`, `.../carroll.py`.
- `src/shmoney/orchestrator.py`.
- `src/shmoney/cli.py` — `typer` commands: `init`, `run`, `reaudit`, `sheet-sync`, `reset`.
- `tests/` — mirror of `src/` for unit tests + `tests/fixtures/` for recorded source payloads.
- `.env.example`, `.gitignore`.
