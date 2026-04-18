from typing import Protocol


COLUMNS: list[str] = [
    "Business Name",
    "Owner Name",
    "Phone Number",
    "Address",
    "Neighborhood / Area",
    "Business Type",
    "Source Found",
    "Online Presence\n(1-5 scale)",
    "Website URL\n(if any)",
    "Yelp / Google Listing?",
    "# of Reviews",
    "Estimated Annual Revenue",
    "Years in Business",
    "Pain Points / Notes",
    "Consulting Opportunity",
    "Contact Status",
    "Follow-Up Date",
    "Outcome / Next Step",
    "Canonical Key",
]

OPERATOR_COLUMNS: set[str] = {
    "Pain Points / Notes",
    "Contact Status",
    "Follow-Up Date",
    "Outcome / Next Step",
}

CANONICAL_KEY_COL = "Canonical Key"


class WorksheetClient(Protocol):
    def get_all_values(self) -> list[list[str]]: ...
    def append_row(self, values: list[str]) -> None: ...
    def batch_update_cells(self, updates: list[tuple[int, int, str]]) -> None: ...


class SheetsWriter:
    def __init__(self, worksheet: WorksheetClient):
        self.ws = worksheet
        self._loaded = False
        self._header_index: dict[str, int] = {}
        self._key_to_row: dict[str, int] = {}
        self._row_count: int = 0

    def ensure_schema(self) -> None:
        values = self.ws.get_all_values()
        if not values or not any(values[0]):
            self.ws.append_row(COLUMNS)
            self._header_index = {c: i for i, c in enumerate(COLUMNS)}
            self._row_count = 1
            self._loaded = True
            return
        header = list(values[0])
        if CANONICAL_KEY_COL not in header:
            header.append(CANONICAL_KEY_COL)
            self.ws.batch_update_cells([(1, len(header), CANONICAL_KEY_COL)])
        missing = [c for c in COLUMNS if c not in header]
        if missing:
            raise ValueError(f"Sheet missing columns: {missing}")
        self._header_index = {c: i for i, c in enumerate(header)}
        key_col = self._header_index[CANONICAL_KEY_COL]
        for idx, row in enumerate(values[1:], start=2):
            if len(row) > key_col and row[key_col]:
                self._key_to_row[row[key_col]] = idx
        self._row_count = len(values)
        self._loaded = True

    def upsert(self, canonical_key: str, business: dict[str, str]) -> int:
        if not self._loaded:
            self.ensure_schema()
        if canonical_key in self._key_to_row:
            row_idx = self._key_to_row[canonical_key]
            self._update_row(row_idx, business, canonical_key)
            return row_idx
        row = [""] * (max(self._header_index.values()) + 1)
        for col, val in business.items():
            if col in OPERATOR_COLUMNS:
                continue
            if col not in self._header_index:
                continue
            row[self._header_index[col]] = val if val is not None else ""
        row[self._header_index[CANONICAL_KEY_COL]] = canonical_key
        self.ws.append_row(row)
        self._row_count += 1
        row_idx = self._row_count
        self._key_to_row[canonical_key] = row_idx
        return row_idx

    def _update_row(self, row_idx: int, business: dict[str, str], canonical_key: str) -> None:
        updates: list[tuple[int, int, str]] = []
        for col, val in business.items():
            if col in OPERATOR_COLUMNS:
                continue
            if col not in self._header_index:
                continue
            updates.append(
                (row_idx, self._header_index[col] + 1, val if val is not None else "")
            )
        updates.append(
            (row_idx, self._header_index[CANONICAL_KEY_COL] + 1, canonical_key)
        )
        if updates:
            self.ws.batch_update_cells(updates)
