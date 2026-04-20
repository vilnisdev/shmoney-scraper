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
pipeline sheet-sync      # rebuild Sheet rows from SQLite (e.g. after an accidental delete)
pipeline reset           # wipe SQLite only (not the Sheet). --yes skips the prompt.
pipeline enrich          # fill Owner Name / formation date on existing rows via SDAT.
```

## Enrichment (SDAT Business Express)

`pipeline enrich --source md-sdat-direct` iterates `businesses WHERE owner_name
IS NULL`, queries Maryland SDAT's public Business Express portal, and fills
`owner_name` (officer for corporations, resident agent for LLCs) and
`registered_at` via the existing COALESCE upsert. Hit rate is realistically
30–45% — corporations are the reliable wins; sole proprietorships aren't in
SDAT at all.

Ban-avoidance defaults (intentionally conservative, not operator-configurable):
- 4s min interval ± 1s jitter between requests.
- Single-threaded, single session.
- `robots.txt` consulted at init; refusal aborts the run.
- 429 / challenge-page response = immediate `SdatCircuitBreakerOpen` abort,
  watermark saved so next run resumes.
- 5xx tolerated once with a 10s backoff; second failure aborts.
- Every response cached to `data/sdat_cache/` — reruns never re-hit the portal.
- `--limit` hard-capped at 200.

```bash
pipeline enrich --dry-run              # prints candidate list, zero network
pipeline enrich --limit 25             # default; processes up to 25 rows
```

First-time use: capture a real search-results + detail HTML pair from the live
portal and sanity-check that the parsers in `src/shmoney/sources/sdat_direct.py`
find the expected field labels. Fixtures under `tests/` are synthetic.

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
