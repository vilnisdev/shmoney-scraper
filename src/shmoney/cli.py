import typer

from .config import load_config
from .orchestrator import run_once
from .repo import Repository
from .sheets import SheetsWriter
from .sources.sdat import MarylandSDATAdapter
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
            if not cfg.sdat_resource_path:
                raise typer.BadParameter(
                    "SDAT_RESOURCE_PATH env var required for source 'md-sdat'"
                )
            adapter = MarylandSDATAdapter(
                resource_path=cfg.sdat_resource_path,
                base_url=cfg.sdat_base_url,
                app_token=cfg.sdat_app_token,
                limit=limit,
            )
        else:
            raise typer.BadParameter(f"source {source!r} not implemented")
        ws = _GspreadWorksheetAdapter(_gspread_worksheet(cfg))
        writer = SheetsWriter(ws)
        n = run_once(adapter, repo, writer)
        typer.echo(f"upserted {n} rows")
    finally:
        repo.close()


def main() -> None:
    app()
