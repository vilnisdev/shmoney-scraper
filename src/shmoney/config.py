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
    opencorporates_api_token: str | None
    opencorporates_query: str
    brave_api_key: str | None


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
        opencorporates_api_token=os.environ.get("OPENCORPORATES_API_TOKEN") or None,
        opencorporates_query=os.environ.get("OPENCORPORATES_QUERY", ""),
        brave_api_key=os.environ.get("BRAVE_API_KEY") or None,
    )
