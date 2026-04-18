import json
import sqlite3
from pathlib import Path
from typing import Optional

from .sources.base import RawBusiness

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fetches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_raw_fetches_key ON raw_fetches(canonical_key);

CREATE TABLE IF NOT EXISTS businesses (
    canonical_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    phone TEXT,
    website TEXT,
    neighborhood TEXT,
    business_type TEXT,
    source TEXT NOT NULL,
    yelp_or_google_listing TEXT,
    review_count INTEGER,
    owner_name TEXT,
    registered_at TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sheet_row_map (
    canonical_key TEXT PRIMARY KEY,
    row_index INTEGER NOT NULL,
    last_written_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Repository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def record_raw(self, source: str, canonical_key: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO raw_fetches (source, canonical_key, payload) VALUES (?, ?, ?)",
            (source, canonical_key, json.dumps(payload, default=str)),
        )

    def upsert_business(self, canonical_key: str, raw: RawBusiness) -> None:
        self.conn.execute(
            """
            INSERT INTO businesses (
                canonical_key, name, address, phone, website, neighborhood,
                business_type, source, yelp_or_google_listing, review_count,
                owner_name, registered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                name = COALESCE(businesses.name, excluded.name),
                address = COALESCE(businesses.address, excluded.address),
                phone = COALESCE(excluded.phone, businesses.phone),
                website = COALESCE(excluded.website, businesses.website),
                neighborhood = COALESCE(excluded.neighborhood, businesses.neighborhood),
                business_type = COALESCE(excluded.business_type, businesses.business_type),
                source = COALESCE(businesses.source, excluded.source),
                yelp_or_google_listing = COALESCE(excluded.yelp_or_google_listing, businesses.yelp_or_google_listing),
                review_count = COALESCE(excluded.review_count, businesses.review_count),
                owner_name = COALESCE(excluded.owner_name, businesses.owner_name),
                registered_at = COALESCE(excluded.registered_at, businesses.registered_at),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                canonical_key,
                raw.name,
                raw.address,
                raw.phone,
                raw.website,
                raw.neighborhood,
                raw.business_type,
                raw.source,
                raw.yelp_or_google_listing,
                raw.review_count,
                raw.owner_name,
                raw.registered_at,
            ),
        )

    def get_business(self, canonical_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM businesses WHERE canonical_key = ?", (canonical_key,)
        ).fetchone()
        return dict(row) if row else None

    def record_sheet_row(self, canonical_key: str, row_index: int) -> None:
        self.conn.execute(
            """
            INSERT INTO sheet_row_map (canonical_key, row_index) VALUES (?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                row_index = excluded.row_index,
                last_written_at = CURRENT_TIMESTAMP
            """,
            (canonical_key, row_index),
        )

    def get_sheet_row(self, canonical_key: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT row_index FROM sheet_row_map WHERE canonical_key = ?",
            (canonical_key,),
        ).fetchone()
        return int(row["row_index"]) if row else None

    def close(self) -> None:
        self.conn.close()
