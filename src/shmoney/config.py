import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    yelp_api_key: str
    google_sheet_id: str
    google_sa_json_path: Path
    db_path: Path
    sdat_base_url: str
    sdat_resource_path: str
    sdat_app_token: str | None


def load_config() -> Config:
    load_dotenv()
    required = ["YELP_API_KEY", "GOOGLE_SHEET_ID", "GOOGLE_SA_JSON_PATH"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars: {missing}")
    return Config(
        yelp_api_key=os.environ["YELP_API_KEY"],
        google_sheet_id=os.environ["GOOGLE_SHEET_ID"],
        google_sa_json_path=Path(os.environ["GOOGLE_SA_JSON_PATH"]),
        db_path=Path(os.environ.get("DB_PATH", "data/pipeline.db")),
        sdat_base_url=os.environ.get("SDAT_BASE_URL", "https://opendata.maryland.gov"),
        sdat_resource_path=os.environ.get("SDAT_RESOURCE_PATH", ""),
        sdat_app_token=os.environ.get("SDAT_APP_TOKEN") or None,
    )
