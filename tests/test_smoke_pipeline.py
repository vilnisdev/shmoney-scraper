from shmoney.audit import AuditFlags, AuditReport
from shmoney.canonicalize import canonical_key
from shmoney.orchestrator import run_once
from shmoney.repo import Repository
from shmoney.sheets import COLUMNS, SheetsWriter
from shmoney.sources.base import RawBusiness, SourceAdapter


class _StrongAuditor:
    def audit(self, url):
        return AuditReport(
            flags=AuditFlags(
                reachable=True, https=True, redirects_to_https=False,
                has_viewport=True, body_substantial=True,
                response_time_ok=True, last_modified_fresh=True,
            ),
            fetched_at="2026-04-19T00:00:00+00:00",
            status_code=200,
            elapsed_ms=100,
        )

    def close(self):
        pass


class StubAdapter(SourceAdapter):
    source_name = "yelp"

    def __init__(self, rows, source_name="yelp"):
        self._rows = rows
        self.source_name = source_name

    def iter_businesses(self):
        yield from self._rows


def _col(name: str) -> int:
    return COLUMNS.index(name)


def test_smoke_end_to_end(tmp_path, fake_ws):
    raw = RawBusiness(
        source="yelp",
        name="Joe's Pizza",
        address="123 Main St, Baltimore, MD",
        phone="410-555-1234",
        website=None,
        neighborhood="Federal Hill",
        business_type="Pizza",
        yelp_or_google_listing="https://yelp.com/biz/joes",
        review_count=42,
    )
    adapter = StubAdapter([raw])
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)

    n = run_once(adapter, repo, writer)

    assert n == 1
    # Sheet has header + 1 row
    assert len(fake_ws.rows) == 2
    row = fake_ws.rows[1]
    assert row[_col("Business Name")] == "Joe's Pizza"
    assert row[_col("Phone Number")] == "410-555-1234"
    assert row[_col("Address")].startswith("123 Main St")
    assert row[_col("Source Found")] == "yelp"
    assert row[_col("Canonical Key")]  # populated

    # SQLite has rows in all three tables
    key = canonical_key(raw.name, raw.address)
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM raw_fetches WHERE canonical_key=?", (key,)
        ).fetchone()[0]
        == 1
    )
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE canonical_key=?", (key,)
        ).fetchone()[0]
        == 1
    )
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM sheet_row_map WHERE canonical_key=?", (key,)
        ).fetchone()[0]
        == 1
    )


def test_real_website_filtered_out(tmp_path, fake_ws):
    has_real = RawBusiness(
        source="yelp", name="Good Biz", address="1 Main", website="https://goodbiz.com"
    )
    no_site = RawBusiness(source="yelp", name="Bad Biz", address="2 Main", website=None)
    social = RawBusiness(
        source="yelp",
        name="Social Biz",
        address="3 Main",
        website="https://facebook.com/socialbiz",
    )
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    n = run_once(StubAdapter([has_real, no_site, social]), repo, writer, _StrongAuditor())
    assert n == 2  # real-site business excluded
    tags = [fake_ws.rows[r][_col("Consulting Opportunity")] for r in (1, 2)]
    assert "no website" in tags
    assert "social-only (facebook)" in tags


def test_sdat_merges_into_existing_yelp_row(tmp_path, fake_ws):
    yelp_raw = RawBusiness(
        source="yelp",
        name="Joe's Pizza",
        address="123 Main St, Baltimore, MD 21230",
        phone="410-555-1234",
        website=None,
        yelp_or_google_listing="https://yelp.com/biz/joes",
    )
    sdat_raw = RawBusiness(
        source="md-sdat",
        name="JOE'S PIZZA LLC",
        address="123 Main St, Baltimore, MD 21230",
        owner_name="Jane Doe",
        registered_at="2019-03-15",
    )
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)

    run_once(StubAdapter([yelp_raw], source_name="yelp"), repo, writer)
    run_once(StubAdapter([sdat_raw], source_name="md-sdat"), repo, writer)

    assert len(fake_ws.rows) == 2
    row = fake_ws.rows[1]
    assert row[_col("Business Name")] == "Joe's Pizza"
    assert row[_col("Phone Number")] == "410-555-1234"
    assert row[_col("Owner Name")] == "Jane Doe"
    assert row[_col("Source Found")] == "yelp"
    assert row[_col("Years in Business")] != ""
    assert int(row[_col("Years in Business")]) >= 5


def test_rerun_does_not_duplicate(tmp_path, fake_ws):
    raw = RawBusiness(
        source="yelp",
        name="Joe's Pizza",
        address="123 Main St, Baltimore, MD",
        phone="410-555-1234",
    )
    adapter = StubAdapter([raw])
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)

    run_once(adapter, repo, writer)
    run_once(StubAdapter([raw]), repo, SheetsWriter(fake_ws))

    # Sheet still has only header + 1 row
    assert len(fake_ws.rows) == 2
    # businesses + sheet_row_map are unique by key
    assert repo.conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM sheet_row_map").fetchone()[0] == 1
    # raw_fetches is append-only
    assert repo.conn.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0] == 2
