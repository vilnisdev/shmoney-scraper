# shmoney-scraper

Baltimore-metro small-business lead pipeline. Pulls records from public
license feeds and Yelp, deduplicates by canonicalized name+address, audits
any discovered websites, scores online presence 1–5, and writes qualified
leads to a Google Sheet.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/vilnisdev/shmoney-scraper.git
cd shmoney-scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the test suite to confirm the install:

```bash
pytest
```

## Credentials

Copy `.env.example` to `.env` (or export directly) and fill in:

| Var | Required | What it is |
| --- | --- | --- |
| `YELP_API_KEY` | yes | [Yelp Fusion](https://docs.developer.yelp.com/) API key. |
| `GOOGLE_SHEET_ID` | yes | ID segment of the target Sheet URL. |
| `GOOGLE_SA_JSON_PATH` | yes | Path to Google Cloud service-account JSON. |
| `DB_PATH` | no | SQLite path. Defaults to `data/pipeline.db`. |
| `OPENCORPORATES_API_TOKEN` | for `--source md-sdat` | OpenCorporates Maryland feed. |
| `OPENCORPORATES_QUERY` | no | Override the search term for md-sdat. |
| `BRAVE_API_KEY` | for `discover-websites` | [Brave Search API](https://brave.com/search/api/) key; 2000 free queries/month. |

### Provisioning a Google service account

1. In Google Cloud Console: create a project, enable the **Google Sheets
   API**, create a service account, and download its JSON key.
2. Point `GOOGLE_SA_JSON_PATH` at that file.
3. Share the target Sheet with the service account's `client_email`
   (Editor). Without this, writes fail with a 403.

## One-shot usage

Initialize the DB + Sheet header:

```bash
pipeline init
```

Pull 20 Yelp restaurant records in Baltimore:

```bash
pipeline run --source yelp --location "Baltimore, MD" --term "restaurants" --limit 20
```

Run summary line printed at end:

```
[yelp] fetched=20 canonicalized=20 qualified_out=4 written=16
```

## Per-source usage

| Source | Command | Notes |
| --- | --- | --- |
| Yelp Fusion | `pipeline run --source yelp --term "…"` | Uses `YELP_API_KEY`. |
| OpenCorporates (MD SDAT) | `pipeline run --source md-sdat` | Uses `OPENCORPORATES_API_TOKEN`. |
| Baltimore City MBE/WBE | `pipeline run --source baltimore-city` | Public ArcGIS feed. |
| Howard County liquor | `pipeline run --source howard` | Public Socrata feed. |
| Anne Arundel liquor | `pipeline run --source anne-arundel` | Public ArcGIS feed. |

Jurisdictions without a public feed (`harford`, `carroll`, `baltimore-county`)
fail loudly and point at `docs/license-sources.md`.

## Operational flags

| Flag | Effect |
| --- | --- |
| `--limit N` | Stop after N raw records per source. |
| `--dry-run` | Full pipeline, but skip Sheet writes + `sheet_row_map` writes. |
| `--reaudit` | Bypass the 30-day audit freshness cache and re-audit. |

License adapters checkpoint their ArcGIS/Socrata offset after each page to
`source_watermarks`. If a run is killed, the next invocation resumes from
the last checkpointed offset. Successful runs clear the watermark.

## Subcommands

```bash
pipeline sheet-sync              # rebuild Sheet rows from SQLite in priority order
pipeline reset                   # wipe SQLite only (not the Sheet). --yes skips the prompt.
pipeline discover-websites       # fill missing websites via DuckDuckGo (strict match)
```

### `discover-websites`

For every row in `businesses` where `website IS NULL`, query the **Brave
Search API**, fuzzy-match the top 5 results against the business name,
verify via domain-name match OR homepage (+ /contact + /about) mentions
of city / zip / phone, and backfill `website` only when gates pass.

Purpose: the adapter feeds (MBE `user_website`, Yelp's URL field) miss
many real websites. Without discovery, the priority-ordered Sheet surfaces
"no-website" rows that actually have sites, diluting the high-value leads.

**Setup**: sign up at https://brave.com/search/api/ (free tier: 2000
queries/month, 1 qps). Put the key in `.env`:

```
BRAVE_API_KEY=...
```

Defaults:
- 1.1s min interval ± 0.2s jitter (matches Brave's 1 qps free-tier cap).
- 429 / auth failure / challenge → circuit-breaker abort.
- All search + homepage responses cached under `data/discovery_cache/`.
- `--limit` hard-capped at 500.
- **Monthly budget hard-stop at 900 queries** (buffer under Brave's
  2000/mo free tier). Persistent per-UTC-month counter in SQLite. Cache
  hits don't consume budget. Raise with `--monthly-budget N` if you've
  moved to Brave's paid tier and accept the overage cost.

```bash
pipeline discover-websites --dry-run          # prints candidate list, zero network
pipeline discover-websites --limit 50         # conservative first pass
pipeline sheet-sync                           # re-apply priority ordering
```

An earlier DuckDuckGo backend was abandoned (see closed issue #38) — DDG's
`/html/` endpoint actively shims scrapers and returned 403 after ~100
queries.

## Diagnostics

```bash
python scripts/find_joins.py
```

Prints every `canonical_key` in `raw_fetches` that appears under more than
one `source` — i.e. cross-source matches where `owner_name` / `phone`
should have merged via COALESCE.

## Troubleshooting

- **`missing env vars: [...]`** — copy `.env.example`, fill in the listed keys, re-run.
- **`gspread.exceptions.APIError: 403`** — service account lacks Editor access to the Sheet. Share the Sheet with the service account email.
- **`Invalid value: source 'foo' not implemented`** — see `docs/license-sources.md` for the supported list and deferral rationale.
- **Sheet rows disappeared** — run `pipeline sheet-sync` to rebuild from SQLite.
- **Stuck on an old cursor** — `sqlite3 data/pipeline.db 'DELETE FROM source_watermarks WHERE source = ?'` with the adapter's source_name (e.g. `howard-license`).
- **Owner Name column blank outside Baltimore City** — expected. The OpenCorporates (md-sdat) source is the fallback, but access is gated; see `docs/license-sources.md`.

## Further reading

- `docs/license-sources.md` — per-jurisdiction feed status.
