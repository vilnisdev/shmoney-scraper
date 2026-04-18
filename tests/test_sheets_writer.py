import pytest

from shmoney.sheets import CANONICAL_KEY_COL, COLUMNS, SheetsWriter


def _col(name: str) -> int:
    return COLUMNS.index(name)


def test_empty_sheet_gets_header(fake_ws):
    SheetsWriter(fake_ws).ensure_schema()
    assert fake_ws.rows[0] == COLUMNS


def test_upsert_appends_new_row_with_key(fake_ws):
    w = SheetsWriter(fake_ws)
    idx = w.upsert(
        "key1",
        {
            "Business Name": "Joe's",
            "Phone Number": "410-555-1234",
            "Address": "1 Main",
            "Source Found": "yelp",
        },
    )
    assert idx == 2  # header is row 1
    assert len(fake_ws.rows) == 2
    row = fake_ws.rows[1]
    assert row[_col("Business Name")] == "Joe's"
    assert row[_col("Phone Number")] == "410-555-1234"
    assert row[_col("Address")] == "1 Main"
    assert row[_col("Source Found")] == "yelp"
    assert row[_col(CANONICAL_KEY_COL)] == "key1"


def test_upsert_existing_key_updates_same_row(fake_ws):
    w = SheetsWriter(fake_ws)
    w.upsert("key1", {"Business Name": "Joe's", "Phone Number": "410"})
    idx2 = w.upsert("key1", {"Business Name": "Joe's", "Phone Number": "410-555-9999"})
    assert idx2 == 2
    assert len(fake_ws.rows) == 2
    assert fake_ws.rows[1][_col("Phone Number")] == "410-555-9999"


def test_reopening_preserves_existing_keys(fake_ws):
    SheetsWriter(fake_ws).upsert("key1", {"Business Name": "First"})
    w2 = SheetsWriter(fake_ws)
    w2.upsert("key1", {"Business Name": "Updated"})
    assert len(fake_ws.rows) == 2
    assert fake_ws.rows[1][_col("Business Name")] == "Updated"


def test_operator_columns_are_never_written(fake_ws):
    w = SheetsWriter(fake_ws)
    w.upsert("key1", {"Business Name": "Joe's"})
    fake_ws.rows[1][_col("Contact Status")] = "Called 3/15"
    fake_ws.rows[1][_col("Pain Points / Notes")] = "Old menu"
    w2 = SheetsWriter(fake_ws)
    w2.upsert(
        "key1",
        {
            "Business Name": "Joe's Updated",
            "Contact Status": "SHOULD_NOT_OVERWRITE",
            "Pain Points / Notes": "SHOULD_NOT_OVERWRITE",
        },
    )
    assert fake_ws.rows[1][_col("Contact Status")] == "Called 3/15"
    assert fake_ws.rows[1][_col("Pain Points / Notes")] == "Old menu"
    assert fake_ws.rows[1][_col("Business Name")] == "Joe's Updated"


def test_missing_column_raises(fake_ws):
    fake_ws.append_row(["Business Name", "Phone Number"])
    with pytest.raises(ValueError, match="missing columns"):
        SheetsWriter(fake_ws).ensure_schema()


def test_upsert_with_extra_trailing_column(fake_ws):
    from shmoney.sheets import COLUMNS as _C

    fake_ws.append_row(list(_C) + ["Spare"])
    w = SheetsWriter(fake_ws)
    w.upsert("key1", {"Business Name": "Joe's", "Address": "1 Main"})
    assert len(fake_ws.rows) == 2
    assert fake_ws.rows[1][_col("Canonical Key")] == "key1"
    assert fake_ws.rows[1][_col("Business Name")] == "Joe's"


def test_ensure_schema_auto_adds_canonical_key(fake_ws):
    from shmoney.sheets import COLUMNS as _C

    header_without_key = [c for c in _C if c != "Canonical Key"]
    fake_ws.append_row(header_without_key)
    SheetsWriter(fake_ws).ensure_schema()
    assert fake_ws.rows[0][-1] == "Canonical Key"


def test_ensure_schema_refuses_to_auto_add_when_data_exists(fake_ws):
    from shmoney.sheets import COLUMNS as _C

    header_without_key = [c for c in _C if c != "Canonical Key"]
    fake_ws.append_row(header_without_key)
    fake_ws.append_row(["existing business"] + [""] * (len(header_without_key) - 1))
    with pytest.raises(ValueError, match="Canonical Key"):
        SheetsWriter(fake_ws).ensure_schema()
