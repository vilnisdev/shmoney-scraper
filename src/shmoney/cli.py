import typer

from .audit import WebsiteAuditor
from .config import load_config
from .orchestrator import run_once
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
    def __init__(self, ws):
        self._ws = ws

    def get_all_values(self):
        return self._ws.get_all_values()

    def append_row(self, values):
        self._ws.append_row(values, value_input_option="RAW")

    def batch_update_cells(self, updates):
        import gspread

        cells = [gspread.Cell(r, c, v) for r, c, v in updates]
        if cells:
            self._ws.update_cells(cells, value_input_option="RAW")


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
) -> None:
    cfg = load_config()
    repo = Repository(cfg.db_path)
    try:
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
            adapter = HowardLicenseAdapter(limit=limit)
        elif source == "baltimore-city":
            adapter = BaltimoreCityLicenseAdapter(limit=limit)
        elif source == "anne-arundel":
            adapter = AnneArundelLicenseAdapter(limit=limit)
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
            n = run_once(adapter, repo, writer, auditor)
        finally:
            auditor.close()
        typer.echo(f"upserted {n} rows")
    finally:
        repo.close()


def main() -> None:
    app()
