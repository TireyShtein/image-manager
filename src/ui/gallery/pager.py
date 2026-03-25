from typing import NamedTuple
from src.ui.gallery.constants import PAGE_SIZE


class LoadResult(NamedTuple):
    shown: int
    sfw_hidden: int        # files on disk, filtered out by SFW Mode
    missing: int           # files not found on disk at all


class GalleryPager:
    """Holds all rows (lightweight dicts) and serves fixed-size pages."""
    def __init__(self, rows: list):
        # Store as plain dicts to avoid holding sqlite3.Row refs across pages
        self._rows = [{"id": r["id"], "path": r["path"]} for r in rows]
        self._page = 0

    @property
    def total(self) -> int:
        return len(self._rows)

    @property
    def page_count(self) -> int:
        return max(1, (len(self._rows) + PAGE_SIZE - 1) // PAGE_SIZE)

    @property
    def current_page(self) -> int:
        return self._page

    def get_page(self, page: int) -> list[dict]:
        self._page = max(0, min(page, self.page_count - 1))
        start = self._page * PAGE_SIZE
        return self._rows[start:start + PAGE_SIZE]

    def remove(self, image_id: int):
        self._rows = [r for r in self._rows if r["id"] != image_id]
        # Clamp page in case we removed the last item on the last page
        self._page = min(self._page, self.page_count - 1)

    def all_items(self) -> list[tuple[int, str]]:
        return [(r["id"], r["path"]) for r in self._rows]
