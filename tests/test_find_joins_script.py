import sqlite3
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from find_joins import find_cross_source_keys  # noqa: E402


def _seed(db_path: Path, rows: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE raw_fetches (canonical_key TEXT, source TEXT)"
        )
        conn.executemany(
            "INSERT INTO raw_fetches (canonical_key, source) VALUES (?, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def test_reports_keys_with_multiple_sources(tmp_path):
    db = tmp_path / "t.db"
    _seed(
        db,
        [
            ("k1", "yelp"),
            ("k1", "baltimore-city-license"),
            ("k2", "yelp"),
            ("k3", "howard-license"),
            ("k3", "md-sdat"),
        ],
    )
    result = find_cross_source_keys(db)
    assert set(result.keys()) == {"k1", "k3"}
    assert result["k1"] == {"yelp", "baltimore-city-license"}
    assert result["k3"] == {"howard-license", "md-sdat"}


def test_same_source_twice_is_not_a_join(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [("k1", "yelp"), ("k1", "yelp")])
    assert find_cross_source_keys(db) == {}


def test_empty_raw_fetches(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [])
    assert find_cross_source_keys(db) == {}
