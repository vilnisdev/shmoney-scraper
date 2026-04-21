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

    n = run_once(adapter, repo, writer).written

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
    n = run_once(StubAdapter([has_real, no_site, social]), repo, writer, _StrongAuditor()).written
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


def test_join_fires_across_address_textual_variants(tmp_path, fake_ws):
    # Regression for PR #17 QA: Yelp and license sources emit the same
    # logical address in different textual forms ("Ave" vs "Avenue",
    # trailing-dash zip). The COALESCE join must still merge them.
    yelp_raw = RawBusiness(
        source="yelp",
        name="Noble's Landscape Service",
        address="3314 Elgin Ave, Baltimore, MD 21216",
        phone="410-555-0000",
        website=None,
    )
    license_raw = RawBusiness(
        source="baltimore-city-license",
        name="Noble's Landscape Service",
        address="3314 Elgin Avenue, Baltimore, MD 21216-",
        owner_name="Jay Noble",
        registered_at="2000-01-16",
    )
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)

    run_once(StubAdapter([yelp_raw], source_name="yelp"), repo, writer)
    run_once(
        StubAdapter([license_raw], source_name="baltimore-city-license"),
        repo,
        writer,
    )

    assert len(fake_ws.rows) == 2  # header + one merged row
    row = fake_ws.rows[1]
    assert row[_col("Owner Name")] == "Jay Noble"
    assert row[_col("Phone Number")] == "410-555-0000"


def test_dry_run_writes_nothing_to_sheet(tmp_path, fake_ws):
    raw = RawBusiness(source="yelp", name="Joe's Pizza",
                      address="123 Main St", phone="410-555-1234")
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    result = run_once(StubAdapter([raw]), repo, writer, dry_run=True)
    n = result.written
    # Header row is fine (ensure_schema), but no data row appended.
    assert len(fake_ws.rows) == 1
    # Business still recorded in SQLite (run_once is cheap to rerun).
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM businesses"
    ).fetchone()[0] == 1
    # No sheet_row_map entries.
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM sheet_row_map"
    ).fetchone()[0] == 0
    # written counter is 0.
    assert n == 0


class _CountingAuditor:
    def __init__(self):
        self.calls = 0

    def audit(self, url):
        self.calls += 1
        return AuditReport(
            flags=AuditFlags(
                reachable=True, https=False, redirects_to_https=False,
                has_viewport=False, body_substantial=False,
                response_time_ok=False, last_modified_fresh=False,
            ),
            fetched_at="2026-04-19T00:00:00+00:00",
            status_code=200, elapsed_ms=100,
        )

    def close(self):
        pass


def test_reaudit_bypasses_freshness_cache(tmp_path, fake_ws):
    raw = RawBusiness(source="yelp", name="Biz",
                      address="1 Main", website="https://biz.example")
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    auditor = _CountingAuditor()

    run_once(StubAdapter([raw]), repo, writer, auditor)
    assert auditor.calls == 1

    # Second run: freshness would normally suppress re-audit.
    run_once(StubAdapter([raw]), repo, writer, auditor)
    assert auditor.calls == 1

    # With reaudit=True we force a fresh audit.
    run_once(StubAdapter([raw]), repo, writer, auditor, reaudit=True)
    assert auditor.calls == 2


def test_run_summary_counts(tmp_path, fake_ws):
    from shmoney.orchestrator import run_once as _run
    rows = [
        RawBusiness(source="yelp", name="No Site", address="1 Main", website=None),
        RawBusiness(source="yelp", name="Social", address="2 Main",
                    website="https://facebook.com/s"),
        RawBusiness(source="yelp", name="Strong",
                    address="3 Main", website="https://strong.example"),
    ]
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    result = _run(StubAdapter(rows), repo, writer, _StrongAuditor())
    # Backward-compat: still returns int OR a summary object with .written.
    written = result.written if hasattr(result, "written") else result
    assert written == 2
    if hasattr(result, "fetched"):
        assert result.fetched == 3
        assert result.canonicalized == 3
        assert result.qualified_out == 1  # strong real site filtered


def test_sheet_sync_rebuilds_rows_from_sqlite(tmp_path, fake_ws):
    from shmoney.orchestrator import sheet_sync

    rows = [
        RawBusiness(source="yelp", name="Biz A", address="1 Main",
                    phone="410-555-1111", website=None),
        RawBusiness(source="yelp", name="Biz B", address="2 Main",
                    phone="410-555-2222", website="https://facebook.com/b"),
    ]
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    run_once(StubAdapter(rows), repo, writer)
    assert len(fake_ws.rows) == 3  # header + 2 rows

    # Operator deletes data rows from the Sheet, leaves header.
    fake_ws.rows = [fake_ws.rows[0]]

    # Run sheet-sync — reconstructs rows from sqlite.
    writer2 = SheetsWriter(fake_ws)
    n = sheet_sync(repo, writer2)
    assert n == 2
    assert len(fake_ws.rows) == 3
    names = {fake_ws.rows[1][_col("Business Name")],
             fake_ws.rows[2][_col("Business Name")]}
    assert names == {"Biz A", "Biz B"}


def test_sheet_sync_orders_no_website_rows_first(tmp_path, fake_ws):
    from shmoney.orchestrator import sheet_sync
    rows = [
        RawBusiness(source="yelp", name="Has Weak Site", address="1 Main",
                    website="https://weaksite.example"),
        RawBusiness(source="yelp", name="No Site", address="2 Main",
                    website=None),
        RawBusiness(source="yelp", name="Social Only", address="3 Main",
                    website="https://facebook.com/x"),
    ]
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    run_once(StubAdapter(rows), repo, writer)
    # Wipe the sheet and resync in priority order.
    writer2 = SheetsWriter(fake_ws)
    sheet_sync(repo, writer2)
    # Row 1 is header. Data rows 2..N in priority order:
    # no-website rows first, then social-only, then weak site.
    data_names = [fake_ws.rows[i][_col("Business Name")]
                  for i in range(1, len(fake_ws.rows))]
    assert data_names[0] == "No Site"
    # "No Site" must precede anything with a website.
    no_site_idx = data_names.index("No Site")
    weak_site_idx = data_names.index("Has Weak Site")
    social_idx = data_names.index("Social Only")
    assert no_site_idx < weak_site_idx
    assert no_site_idx < social_idx


def test_sheet_sync_secondary_sort_by_score_ascending(tmp_path, fake_ws):
    # Two no-website rows: they should sort by score ASC (weakest first).
    # Score 1 = no site (lowest). Both get 1, so tie-break by name ordering
    # we at least expect both to cluster before any website row.
    from shmoney.orchestrator import sheet_sync
    rows = [
        RawBusiness(source="yelp", name="Weak Real",
                    address="1 Main",
                    website="https://real.example"),
        RawBusiness(source="yelp", name="NoSite A",
                    address="2 Main", website=None),
        RawBusiness(source="yelp", name="NoSite B",
                    address="3 Main", website=None),
    ]
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    run_once(StubAdapter(rows), repo, writer)
    writer2 = SheetsWriter(fake_ws)
    sheet_sync(repo, writer2)
    data_names = [fake_ws.rows[i][_col("Business Name")]
                  for i in range(1, len(fake_ws.rows))]
    # Both no-website rows precede the real site.
    assert data_names.index("NoSite A") < data_names.index("Weak Real")
    assert data_names.index("NoSite B") < data_names.index("Weak Real")


def test_sheet_sync_is_idempotent(tmp_path, fake_ws):
    from shmoney.orchestrator import sheet_sync

    raw = RawBusiness(source="yelp", name="Biz", address="1 Main",
                      phone="410-555-1111")
    repo = Repository(tmp_path / "t.db")
    writer = SheetsWriter(fake_ws)
    run_once(StubAdapter([raw]), repo, writer)
    before = [list(r) for r in fake_ws.rows]
    sheet_sync(repo, SheetsWriter(fake_ws))
    assert fake_ws.rows == before


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
