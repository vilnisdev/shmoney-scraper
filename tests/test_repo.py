from shmoney.repo import Repository
from shmoney.sources.base import RawBusiness


def test_schema_created(tmp_path):
    r = Repository(tmp_path / "t.db")
    tables = {
        row[0]
        for row in r.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"raw_fetches", "businesses", "sheet_row_map"}.issubset(tables)


def test_upsert_business_is_idempotent(tmp_path):
    r = Repository(tmp_path / "t.db")
    raw = RawBusiness(source="yelp", name="Joe", address="1 Main")
    r.upsert_business("key1", raw)
    r.upsert_business("key1", raw)
    count = r.conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    assert count == 1


def test_record_sheet_row(tmp_path):
    r = Repository(tmp_path / "t.db")
    r.record_sheet_row("k", 5)
    assert r.get_sheet_row("k") == 5
    r.record_sheet_row("k", 10)
    assert r.get_sheet_row("k") == 10


def test_record_raw_append_only(tmp_path):
    r = Repository(tmp_path / "t.db")
    r.record_raw("yelp", "k", {"a": 1})
    r.record_raw("yelp", "k", {"a": 2})
    count = r.conn.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0]
    assert count == 2
