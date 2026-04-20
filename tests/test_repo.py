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


def test_watermark_roundtrip(tmp_path):
    r = Repository(tmp_path / "t.db")
    assert r.get_watermark("howard-license") == 0
    r.set_watermark("howard-license", 500)
    assert r.get_watermark("howard-license") == 500
    r.set_watermark("howard-license", 1200)
    assert r.get_watermark("howard-license") == 1200
    r.clear_watermark("howard-license")
    assert r.get_watermark("howard-license") == 0


def test_watermark_scoped_per_source(tmp_path):
    r = Repository(tmp_path / "t.db")
    r.set_watermark("howard-license", 100)
    r.set_watermark("anne-arundel-license", 300)
    assert r.get_watermark("howard-license") == 100
    assert r.get_watermark("anne-arundel-license") == 300


def test_text_watermark_roundtrip(tmp_path):
    r = Repository(tmp_path / "t.db")
    assert r.get_text_watermark("md-sdat-direct") is None
    r.set_text_watermark("md-sdat-direct", "abc123")
    assert r.get_text_watermark("md-sdat-direct") == "abc123"
    r.set_text_watermark("md-sdat-direct", "def456")
    assert r.get_text_watermark("md-sdat-direct") == "def456"


def test_text_and_int_watermarks_coexist(tmp_path):
    r = Repository(tmp_path / "t.db")
    r.set_watermark("howard-license", 500)
    r.set_text_watermark("md-sdat-direct", "key-xyz")
    assert r.get_watermark("howard-license") == 500
    assert r.get_text_watermark("md-sdat-direct") == "key-xyz"


def test_iter_businesses_missing_owner(tmp_path):
    r = Repository(tmp_path / "t.db")
    r.upsert_business("k1", RawBusiness(source="a", name="A", address="1"))
    r.upsert_business("k2", RawBusiness(source="a", name="B", address="2",
                                        owner_name="Bob"))
    r.upsert_business("k3", RawBusiness(source="a", name="C", address="3"))
    rows = list(r.iter_businesses_missing_owner())
    keys = [k for k, _, _ in rows]
    assert keys == ["k1", "k3"]


def test_iter_businesses_missing_owner_respects_limit_and_cursor(tmp_path):
    r = Repository(tmp_path / "t.db")
    for k in ("k1", "k2", "k3", "k4"):
        r.upsert_business(k, RawBusiness(source="a", name=k, address="x"))
    first = [k for k, _, _ in r.iter_businesses_missing_owner(limit=2)]
    assert first == ["k1", "k2"]
    second = [
        k for k, _, _ in r.iter_businesses_missing_owner(limit=2, after_key="k2")
    ]
    assert second == ["k3", "k4"]


def test_reset_wipes_all_tables(tmp_path):
    r = Repository(tmp_path / "t.db")
    raw = RawBusiness(source="yelp", name="Joe", address="1 Main")
    r.record_raw("yelp", "k", {"a": 1})
    r.upsert_business("k", raw)
    r.record_sheet_row("k", 2)
    r.set_watermark("yelp", 10)
    r.reset()
    for table in ("raw_fetches", "businesses", "sheet_row_map", "source_watermarks", "audits"):
        count = r.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, table
