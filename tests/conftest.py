import pytest


class FakeWorksheet:
    def __init__(self):
        self.rows: list[list[str]] = []

    def get_all_values(self) -> list[list[str]]:
        return [list(r) for r in self.rows]

    def append_row(self, values) -> None:
        self.rows.append(list(values))

    def batch_update_cells(self, updates) -> None:
        for r, c, v in updates:
            while len(self.rows) < r:
                self.rows.append([])
            row = self.rows[r - 1]
            while len(row) < c:
                row.append("")
            row[c - 1] = v


@pytest.fixture
def fake_ws():
    return FakeWorksheet()
