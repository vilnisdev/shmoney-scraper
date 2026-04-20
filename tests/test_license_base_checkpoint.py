import httpx

from shmoney.sources.base import RawBusiness
from shmoney.sources.license_base import LicenseAdapterBase


class _FakeAdapter(LicenseAdapterBase):
    source_name = "fake"

    def __init__(self, pages, **kwargs):
        self._pages = pages
        super().__init__(client=httpx.Client(), min_interval_s=0.0, **kwargs)

    def _build_default_client(self):
        return httpx.Client()

    def _fetch_page(self, offset):
        for start, records, more in self._pages:
            if start == offset:
                return records, more
        return [], False

    def _to_raw_business(self, rec):
        return RawBusiness(source=self.source_name, name=rec["n"], address="x")


def test_start_offset_resumes_from_cursor():
    pages = [
        (0, [{"n": "A"}, {"n": "B"}], True),
        (2, [{"n": "C"}, {"n": "D"}], True),
        (4, [], False),
    ]
    adapter = _FakeAdapter(pages, page_size=2, start_offset=2)
    names = [b.name for b in adapter.iter_businesses()]
    assert names == ["C", "D"]


def test_checkpoint_fires_after_each_page():
    pages = [
        (0, [{"n": "A"}, {"n": "B"}], True),
        (2, [{"n": "C"}], True),
        (3, [], False),
    ]
    checkpoints: list[int] = []
    adapter = _FakeAdapter(
        pages, page_size=2, checkpoint=lambda offset: checkpoints.append(offset)
    )
    list(adapter.iter_businesses())
    # After first page of 2 records: offset 2. After second page of 1: offset 3.
    assert checkpoints == [2, 3]


def test_kill_mid_source_resumes_from_watermark(tmp_path):
    from shmoney.repo import Repository

    repo = Repository(tmp_path / "t.db")

    pages = [
        (0, [{"n": "A"}, {"n": "B"}], True),
        (2, [{"n": "C"}, {"n": "D"}], True),
        (4, [{"n": "E"}], False),
    ]
    source = "fake"

    # First run: consume one page then "die" (simulate by tearing down the
    # iterator after 2 emits). Watermark must be persisted for page 0.
    adapter1 = _FakeAdapter(
        pages,
        page_size=2,
        start_offset=repo.get_watermark(source),
        checkpoint=lambda o: repo.set_watermark(source, o),
    )
    it = adapter1.iter_businesses()
    next(it)
    next(it)
    # Force iteration of the checkpoint after page 0 by advancing past the
    # second element (the page-end checkpoint fires before the next fetch).
    next(it, None)  # drives the generator past page 0 boundary
    it.close()

    assert repo.get_watermark(source) == 2

    # Second run: resume from watermark.
    adapter2 = _FakeAdapter(
        pages,
        page_size=2,
        start_offset=repo.get_watermark(source),
        checkpoint=lambda o: repo.set_watermark(source, o),
    )
    names = [b.name for b in adapter2.iter_businesses()]
    assert names == ["C", "D", "E"]


def test_checkpoint_and_start_offset_interleave():
    pages = [
        (5, [{"n": "F"}, {"n": "G"}], True),
        (7, [], False),
    ]
    checkpoints: list[int] = []
    adapter = _FakeAdapter(
        pages,
        page_size=2,
        start_offset=5,
        checkpoint=lambda offset: checkpoints.append(offset),
    )
    names = [b.name for b in adapter.iter_businesses()]
    assert names == ["F", "G"]
    assert checkpoints == [7]
