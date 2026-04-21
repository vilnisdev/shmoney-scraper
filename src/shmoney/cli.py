import typer

from .audit import WebsiteAuditor
from .config import load_config
from .orchestrator import run_once, sheet_sync
from .repo import Repository
from .sheets import SheetsWriter
from .sources.anne_arundel import AnneArundelLicenseAdapter
from .sources.baltimore_city import BaltimoreCityLicenseAdapter
from .sources.howard import HowardLicenseAdapter
from .sources.opencorporates import OpenCorporatesMDAdapter
from .sources.yelp import YelpFusionAdapter

app = typer.Typer(help="Baltimore small business lead pipeline")


def _gspread_worksheet(cfg):
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(
        str(cfg.google_sa_json_path), scopes=scopes
    )
    client = gspread.authorize(creds)
    return client.open_by_key(cfg.google_sheet_id).sheet1


class _GspreadWorksheetAdapter:
    # Google Sheets API caps writes at 60/min/user/project. Sleep between
    # writes to stay under that. 1.1s leaves slack for clock drift.
    _MIN_WRITE_INTERVAL_S = 1.1

    def __init__(self, ws):
        import time as _time
        self._ws = ws
        self._time = _time
        self._last_write_at: float | None = None

    def _throttle(self) -> None:
        if self._last_write_at is not None:
            elapsed = self._time.monotonic() - self._last_write_at
            wait = self._MIN_WRITE_INTERVAL_S - elapsed
            if wait > 0:
                self._time.sleep(wait)
        self._last_write_at = self._time.monotonic()

    def get_all_values(self):
        return self._ws.get_all_values()

    def append_row(self, values):
        self._throttle()
        self._ws.append_row(values, value_input_option="RAW")

    def batch_update_cells(self, updates):
        import gspread

        cells = [gspread.Cell(r, c, v) for r, c, v in updates]
        if cells:
            self._throttle()
            self._ws.update_cells(cells, value_input_option="RAW")

    def clear_data_rows(self):
        self._throttle()
        # batch_clear leaves the row frame intact; resetting values avoids
        # a separate resize + shape mismatch.
        self._ws.batch_clear(["A2:ZZ"])


@app.command()
def init() -> None:
    cfg = load_config()
    repo = Repository(cfg.db_path)
    repo.close()
    ws = _GspreadWorksheetAdapter(_gspread_worksheet(cfg))
    SheetsWriter(ws).ensure_schema()
    typer.echo("pipeline init ok")


@app.command()
def run(
    source: str = "yelp",
    location: str = "Baltimore, MD",
    term: str = "restaurants",
    limit: int = 20,
    dry_run: bool = typer.Option(False, "--dry-run"),
    reaudit: bool = typer.Option(False, "--reaudit"),
) -> None:
    cfg = load_config()
    repo = Repository(cfg.db_path)
    try:
        license_source_names = {
            "howard": "howard-license",
            "baltimore-city": "baltimore-city-license",
            "anne-arundel": "anne-arundel-license",
        }
        start_offset = 0
        checkpoint = None
        if source in license_source_names:
            src_name = license_source_names[source]
            start_offset = repo.get_watermark(src_name)
            checkpoint = lambda o, s=src_name: repo.set_watermark(s, o)

        if source == "yelp":
            adapter = YelpFusionAdapter(
                api_key=cfg.yelp_api_key, location=location, term=term, limit=limit
            )
        elif source == "md-sdat":
            if not cfg.opencorporates_api_token:
                raise typer.BadParameter(
                    "OPENCORPORATES_API_TOKEN env var required for source 'md-sdat'"
                )
            adapter = OpenCorporatesMDAdapter(
                api_token=cfg.opencorporates_api_token,
                query=cfg.opencorporates_query or term,
                limit=limit,
            )
        elif source == "howard":
            adapter = HowardLicenseAdapter(
                limit=limit, start_offset=start_offset, checkpoint=checkpoint
            )
        elif source == "baltimore-city":
            adapter = BaltimoreCityLicenseAdapter(
                limit=limit, start_offset=start_offset, checkpoint=checkpoint
            )
        elif source == "anne-arundel":
            adapter = AnneArundelLicenseAdapter(
                limit=limit, start_offset=start_offset, checkpoint=checkpoint
            )
        elif source in {"harford", "carroll", "baltimore-county"}:
            raise typer.BadParameter(
                f"source {source!r} has no public license feed — see docs/license-sources.md"
            )
        else:
            raise typer.BadParameter(f"source {source!r} not implemented")
        ws = _GspreadWorksheetAdapter(_gspread_worksheet(cfg))
        writer = SheetsWriter(ws)
        auditor = WebsiteAuditor()
        try:
            summary = run_once(
                adapter, repo, writer, auditor, dry_run=dry_run, reaudit=reaudit
            )
        finally:
            auditor.close()
        if source in license_source_names and not dry_run:
            repo.clear_watermark(license_source_names[source])
        typer.echo(
            f"[{summary.source}] fetched={summary.fetched} "
            f"canonicalized={summary.canonicalized} "
            f"qualified_out={summary.qualified_out} written={summary.written}"
            + (" (dry-run)" if dry_run else "")
        )
    finally:
        repo.close()


@app.command("sheet-sync")
def sheet_sync_cmd() -> None:
    cfg = load_config()
    repo = Repository(cfg.db_path)
    try:
        ws = _GspreadWorksheetAdapter(_gspread_worksheet(cfg))
        writer = SheetsWriter(ws)
        n = sheet_sync(repo, writer)
        typer.echo(f"sheet-sync restored {n} rows")
    finally:
        repo.close()


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", help="skip confirmation prompt"),
) -> None:
    cfg = load_config()
    if not yes:
        typer.confirm(
            f"Wipe SQLite DB at {cfg.db_path}? (Sheet is not affected)",
            abort=True,
        )
    repo = Repository(cfg.db_path)
    try:
        repo.reset()
    finally:
        repo.close()
    typer.echo("pipeline reset ok")


_DISCOVER_HARD_MAX = 500


@app.command("discover-websites")
def discover_websites_cmd(
    limit: int = typer.Option(25, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    ignore_robots: bool = typer.Option(
        False,
        "--ignore-robots",
        help="Bypass robots.txt check. DDG /html/ disallows bots; this is a "
             "ToS violation with real IP-ban risk. Use only if you've "
             "accepted that tradeoff.",
    ),
) -> None:
    limit = min(max(limit, 0), _DISCOVER_HARD_MAX)
    cfg = load_config()
    repo = Repository(cfg.db_path)
    try:
        candidates = list(repo.iter_businesses_missing_website(limit=limit))
        typer.echo(
            f"[website-discovery] candidates={len(candidates)} limit={limit} "
            f"dry_run={dry_run}"
        )
        if dry_run:
            for key, name, addr, phone in candidates:
                typer.echo(f"  DRY {key} name={name!r}")
            return
        if not candidates:
            return

        from .discover import (
            WebsiteDiscoverer,
            WebsiteDiscoveryCircuitBreakerOpen,
        )
        from .sources.base import RawBusiness
        from dataclasses import asdict

        if ignore_robots:
            typer.echo(
                "  WARNING --ignore-robots: DDG /html/ disallows bots; "
                "IP-ban risk accepted."
            )
        discoverer = WebsiteDiscoverer(
            cache_dir=cfg.db_path.parent / "discovery_cache",
            check_robots=not ignore_robots,
        )
        queried = matched = aborted_on = 0
        aborted: str | None = None
        try:
            for key, name, addr, phone in candidates:
                queried += 1
                try:
                    result = discoverer.discover(name, addr, phone)
                except WebsiteDiscoveryCircuitBreakerOpen as e:
                    aborted = str(e)
                    typer.echo(f"  ABORT {key}: {aborted}")
                    break
                if result is None:
                    typer.echo(f"  NO-MATCH {key} name={name!r}")
                    continue
                matched += 1
                raw = RawBusiness(
                    source="website-discovery",
                    name=name,
                    address=addr,
                    website=result.url,
                )
                repo.record_raw("website-discovery", key, asdict(raw))
                repo.upsert_business(key, raw)
                typer.echo(
                    f"  OK {key} url={result.url} "
                    f"conf={result.confidence:.2f} reasons={result.reasons}"
                )
        finally:
            discoverer.close()

        typer.echo(
            f"[website-discovery] queried={queried} matched={matched} "
            f"aborted_on={aborted!r}"
        )
        if aborted:
            raise typer.Exit(code=2)
    finally:
        repo.close()


def main() -> None:
    app()
