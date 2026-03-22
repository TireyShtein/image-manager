import os
from PyQt6.QtWidgets import QListView, QAbstractItemView, QStyle
from PyQt6.QtCore import (Qt, QAbstractListModel, QModelIndex, QSize, QRect,
                           QRunnable, QThreadPool, pyqtSignal, QObject, pyqtSlot,
                           QTimer, QPoint)
from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor, QPen
from src.core import thumbnail_cache, database as db
from typing import NamedTuple

# Maximum size stored in the thumbnail cache on disk
CACHE_THUMB_SIZE = 256

# Prefetch / eviction margins (in rows)
_PREFETCH_MARGIN = 40
_EVICT_MARGIN = 80

# Size tiers: (max_count, thumb_px)
_SIZE_TIERS = [
    (4,    300),
    (12,   240),
    (30,   180),
    (80,   140),
    (200,  110),
    (500,   85),
    (700,   78),
]
_MIN_THUMB_SIZE = 70

# Pagination
PAGE_SIZE = 200


def _compute_thumb_size(count: int) -> int:
    for max_count, size in _SIZE_TIERS:
        if count <= max_count:
            return size
    return _MIN_THUMB_SIZE


# Reusable grey placeholder, created lazily
_placeholder_cache: dict[int, QPixmap] = {}


def _get_placeholder(size: int) -> QPixmap:
    if size not in _placeholder_cache:
        pix = QPixmap(size, size)
        pix.fill(QColor(220, 220, 220))
        _placeholder_cache[size] = pix
    return _placeholder_cache[size]

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


class ThumbnailSignals(QObject):
    loaded = pyqtSignal(int, str, int)  # (image_id, thumb_path, token)
    progress = pyqtSignal(int, int)     # (loaded_count, total_count)
    all_loaded = pyqtSignal()


class ThumbnailLoader(QRunnable):
    def __init__(self, image_id: int, image_path: str, signals: ThumbnailSignals, token: int):
        super().__init__()
        self.image_id = image_id
        self.image_path = image_path
        self.signals = signals
        self._token = token
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        try:
            thumb = thumbnail_cache.get_or_create_thumbnail(self.image_path)
        except Exception:
            thumb = None
        # Always emit so the counter increments even on failure
        self.signals.loaded.emit(self.image_id, thumb or "", self._token)


class FolderLoaderSignals(QObject):
    rows_ready = pyqtSignal(list, int, int)  # (rows, token, recovered_count)


class FolderLoaderRunnable(QRunnable):
    def __init__(self, folder: str, media_exts: set, token: int, signals: FolderLoaderSignals):
        super().__init__()
        self._folder = folder
        self._media_exts = media_exts
        self._token = token
        self._signals = signals
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        paths = []
        try:
            with os.scandir(self._folder) as entries:
                for entry in entries:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in self._media_exts:
                        paths.append(entry.path)
        except OSError:
            pass
        sorted_paths = sorted(paths, key=lambda p: os.path.basename(p).lower())
        rows, recovered = db.get_or_create_images_batch(sorted_paths)
        self._signals.rows_ready.emit(rows, self._token, recovered)


class GalleryModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Each item: {"id", "path", "display_pix": QPixmap|None}
        self._items: list[dict] = []
        self._id_index: dict[int, int] = {}
        self._display_size: int = _compute_thumb_size(0)
        self._pool = QThreadPool.globalInstance()
        self._signals = ThumbnailSignals()
        self._signals.loaded.connect(self._on_thumbnail_loaded)
        self._total: int = 0
        self._loaded: int = 0
        self._thumb_token: int = 0
        self._show_folder_origin: bool = False
        # Track which rows have been queued for thumbnail loading
        self._queued: set[int] = set()
        self._error_ids: set[int] = set()

    def set_images(self, rows):
        self.beginResetModel()
        self._items = [{"id": r["id"], "path": r["path"],
                        "display_pix": None} for r in rows]
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self._total = len(self._items)
        self._loaded = 0
        self._queued = set()
        self._error_ids = set()
        self._thumb_token += 1
        self.endResetModel()
        if self._total == 0:
            self._signals.all_loaded.emit()
        # Do NOT call _start_loading() — thumbnails are loaded lazily via
        # request_thumbnails() driven by the viewport scroll position.

    def get_all_items(self) -> list[tuple[int, str]]:
        return [(item["id"], item["path"]) for item in self._items]

    def set_display_size(self, size: int):
        if size == self._display_size:
            return
        self._display_size = size
        # Without source_pix we cannot re-scale in memory.  Clear all
        # display pixmaps and let the lazy loader refetch from disk cache.
        for item in self._items:
            item["display_pix"] = None
        self._queued = set()
        if self._items:
            self._loaded = 0
            self._total = len(self._items)
            self.dataChanged.emit(
                self.index(0),
                self.index(len(self._items) - 1),
                [Qt.ItemDataRole.DecorationRole]
            )

    def _scale(self, pix: QPixmap) -> QPixmap:
        return pix.scaled(
            self._display_size, self._display_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

    def request_thumbnails(self, first_row: int, last_row: int):
        """Queue thumbnail loading for rows in the visible range + margin.

        Only queues items that have no display_pix and are not already queued.
        """
        start = max(0, first_row - _PREFETCH_MARGIN)
        end = min(len(self._items), last_row + _PREFETCH_MARGIN + 1)
        for i in range(start, end):
            if i in self._queued:
                continue
            item = self._items[i]
            if item["display_pix"] is not None:
                continue
            self._queued.add(i)
            loader = ThumbnailLoader(item["id"], item["path"], self._signals, self._thumb_token)
            self._pool.start(loader)

    def _evict_offscreen(self, first_visible: int, last_visible: int):
        """Free pixmap memory for rows far outside the visible range."""
        keep_start = max(0, first_visible - _EVICT_MARGIN)
        keep_end = min(len(self._items), last_visible + _EVICT_MARGIN + 1)
        for i in range(0, keep_start):
            item = self._items[i]
            if item["display_pix"] is not None:
                item["display_pix"] = None
                self._queued.discard(i)
        for i in range(keep_end, len(self._items)):
            item = self._items[i]
            if item["display_pix"] is not None:
                item["display_pix"] = None
                self._queued.discard(i)

    def _on_thumbnail_loaded(self, image_id: int, thumb_path: str, token: int):
        if token != self._thumb_token:
            return  # stale result from a previous set_images() batch
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        # If the item was evicted before the loader finished, discard the result
        if idx not in self._queued:
            return
        if thumb_path:
            source = QPixmap(thumb_path)
            self._items[idx]["display_pix"] = self._scale(source)
            index = self.index(idx)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
        self._loaded += 1
        self._signals.progress.emit(self._loaded, self._total)
        if self._loaded >= self._total:
            self._signals.all_loaded.emit()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            pix = item["display_pix"] if item["display_pix"] is not None else _get_placeholder(self._display_size)
            if item["id"] in self._error_ids:
                pix = self._apply_error_overlay(pix)
            return pix
        if role == Qt.ItemDataRole.DisplayRole:
            if self._display_size < 140:
                return None
            if self._show_folder_origin:
                folder = os.path.basename(os.path.dirname(item["path"]))
                return f"{folder}/{os.path.basename(item['path'])}"
            return os.path.basename(item["path"])
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = item["path"]
            if item["id"] in self._error_ids:
                tip += "\n[File operation failed]"
            return tip
        if role == Qt.ItemDataRole.UserRole:
            return item["id"]
        return None

    def get_image_id(self, row: int) -> int | None:
        if 0 <= row < len(self._items):
            return self._items[row]["id"]
        return None

    def remove_image(self, image_id: int):
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        was_loaded = self._items[idx]["display_pix"] is not None
        self.beginRemoveRows(QModelIndex(), idx, idx)
        self._items.pop(idx)
        self._queued.discard(idx)
        # Rebuild index and queued set (row numbers shifted)
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        new_queued = set()
        for q in self._queued:
            if q > idx:
                new_queued.add(q - 1)
            else:
                new_queued.add(q)
        self._queued = new_queued
        self.endRemoveRows()
        self._error_ids.discard(image_id)
        # Adjust counters so all_loaded can still fire after removals
        self._total = max(0, self._total - 1)
        if was_loaded:
            self._loaded = max(0, self._loaded - 1)
        if self._loaded >= self._total:
            self._signals.all_loaded.emit()

    def mark_error(self, image_id: int):
        """Mark a thumbnail with a red error overlay (failed file operation)."""
        self._error_ids.add(image_id)
        idx = self._id_index.get(image_id)
        if idx is not None:
            index = self.index(idx)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def _apply_error_overlay(self, pix: QPixmap) -> QPixmap:
        result = QPixmap(pix)
        painter = QPainter(result)
        painter.fillRect(result.rect(), QColor(220, 40, 40, 70))
        painter.setPen(QPen(QColor(220, 40, 40, 180), 2))
        painter.drawRect(1, 1, result.width() - 2, result.height() - 2)
        painter.setPen(QColor(255, 255, 255))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.fillRect(result.width() - 18, 2, 16, 16, QColor(220, 40, 40, 220))
        painter.drawText(result.width() - 18, 2, 16, 16, Qt.AlignmentFlag.AlignCenter, "!")
        painter.end()
        return result

    def set_show_folder_origin(self, show: bool):
        if show == self._show_folder_origin:
            return
        self._show_folder_origin = show
        if self._items:
            self.dataChanged.emit(self.index(0), self.index(len(self._items) - 1),
                                  [Qt.ItemDataRole.DisplayRole])

    def count(self) -> int:
        return len(self._items)


class GalleryView(QListView):
    image_double_clicked = pyqtSignal(int)
    selection_changed = pyqtSignal(list)
    context_menu_requested = pyqtSignal(list, object)
    empty_context_menu_requested = pyqtSignal(object)  # global pos
    thumbnails_loading = pyqtSignal(int, int)  # (loaded, total)
    thumbnails_ready = pyqtSignal(int)          # total count
    page_changed = pyqtSignal(int, int, int)    # (page, page_count, total)
    tags_recovered = pyqtSignal(int)            # recovered_count (> 0 only)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gallery_model = GalleryModel(self)
        self._empty_text = "No media in this folder"
        self._empty_hint: str | None = None  # override for secondary hint text
        self._loading = False
        self._excluded_rating_tags: list[str] = []
        self._pager: GalleryPager | None = None
        self._load_token: int = 0
        self._folder_loader_signals = FolderLoaderSignals()
        self._folder_loader_signals.rows_ready.connect(self._on_folder_loaded)
        self.setModel(self._gallery_model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(8)
        self.setStyleSheet("""
            QListView::item:hover {
                background: rgba(100, 150, 255, 0.12);
                border-radius: 4px;
            }
            QListView::item:selected {
                background: rgba(80, 130, 255, 0.25);
                border: 2px solid #4a90e2;
                border-radius: 4px;
            }
            QListView::item:selected:!active {
                background: rgba(80, 130, 255, 0.12);
                border: 2px solid #9ab8ef;
                border-radius: 4px;
            }
        """)
        self._apply_size(_compute_thumb_size(0))
        self.doubleClicked.connect(self._on_double_click)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._thumb_progress: tuple[int, int] = (0, 0)
        self._loading_flush_timer = QTimer(self)
        self._loading_flush_timer.setSingleShot(True)
        self._loading_flush_timer.setInterval(50)
        self._loading_flush_timer.timeout.connect(self._flush_thumb_progress)
        self._gallery_model._signals.progress.connect(self._on_thumb_progress_raw)
        self._gallery_model._signals.all_loaded.connect(self._on_all_loaded)
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # Viewport-driven lazy loading: connect scroll to _on_scroll
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _on_scroll(self, *_args):
        """Compute visible row range and request/evict thumbnails accordingly."""
        vp = self.viewport()
        top_index = self.indexAt(vp.rect().topLeft())
        bottom_index = self.indexAt(vp.rect().bottomRight())

        first = top_index.row() if top_index.isValid() else 0
        last = bottom_index.row() if bottom_index.isValid() else self._gallery_model.count() - 1
        if last < 0:
            return

        self._gallery_model.request_thumbnails(first, last)
        self._gallery_model._evict_offscreen(first, last)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._on_scroll()

    def _on_all_loaded(self):
        self._loading = False
        self.viewport().update()
        self.thumbnails_ready.emit(self._gallery_model.count())

    def _on_selection_changed(self, selected, deselected):
        self.selection_changed.emit(self.get_selected_ids())

    def _apply_size(self, thumb_px: int):
        label_px = max(20, thumb_px // 5)  # scales: 70px->20, 140px->28, 300px->60
        self.setIconSize(QSize(thumb_px, thumb_px))
        self.setGridSize(QSize(thumb_px + 16, thumb_px + label_px))
        self._gallery_model.set_display_size(thumb_px)
        QTimer.singleShot(0, self._on_scroll)  # retrigger lazy loading after size change

    def _refresh_size(self):
        count = self._gallery_model.count()
        self._apply_size(_compute_thumb_size(count))

    def _on_thumb_progress_raw(self, loaded: int, total: int):
        self._thumb_progress = (loaded, total)
        if loaded >= total:
            self._loading_flush_timer.stop()
            self._flush_thumb_progress()
        elif not self._loading_flush_timer.isActive():
            self._loading_flush_timer.start()

    def _flush_thumb_progress(self):
        self.thumbnails_loading.emit(*self._thumb_progress)

    def _load_rows(self, rows):
        self._loading_flush_timer.stop()
        self.clearSelection()
        self._loading = False
        self._pager = GalleryPager(rows)
        self._show_page(0)

    def _show_page(self, page: int):
        page_rows = self._pager.get_page(page)
        self._apply_size(_compute_thumb_size(len(page_rows)))
        self._gallery_model.set_images(page_rows)
        self.scrollToTop()
        if page_rows:
            QTimer.singleShot(0, self._on_scroll)
        self.page_changed.emit(
            self._pager.current_page,
            self._pager.page_count,
            self._pager.total,
        )

    def next_page(self):
        if self._pager and self._pager.current_page + 1 < self._pager.page_count:
            self._show_page(self._pager.current_page + 1)

    def prev_page(self):
        if self._pager and self._pager.current_page > 0:
            self._show_page(self._pager.current_page - 1)

    def set_show_folder_origin(self, show: bool):
        """Show 'folder/filename' labels instead of just filenames."""
        self._gallery_model.set_show_folder_origin(show)

    def set_rating_filter(self, excluded: list[str]):
        """Set which rating tags to hide. Pass [] to show everything."""
        self._excluded_rating_tags = excluded

    def _apply_rating_filter(self, rows) -> list:
        if not self._excluded_rating_tags:
            return rows
        ids = [r["id"] for r in rows]
        allowed_ids = set(db.filter_out_images_with_tags(ids, self._excluded_rating_tags))
        return [r for r in rows if r["id"] in allowed_ids]

    def load_folder(self, folder: str):
        self._empty_text = "No media in this folder"
        self._empty_hint = None
        self._loading = True
        self._gallery_model.set_show_folder_origin(False)
        self._load_token += 1
        token = self._load_token
        from src.core.thumbnail_cache import VIDEO_EXTENSIONS
        from src.core.image_scanner import SUPPORTED_EXTENSIONS
        media_exts = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS
        worker = FolderLoaderRunnable(folder, media_exts, token, self._folder_loader_signals)
        QThreadPool.globalInstance().start(worker)

    def _on_folder_loaded(self, rows: list, token: int, recovered: int = 0):
        if token != self._load_token:
            return  # stale — user navigated away before this finished
        self._load_rows(self._apply_rating_filter(rows))
        if recovered > 0:
            self.tags_recovered.emit(recovered)

    def load_images(self, rows, empty_text: str = "No images match this filter",
                    empty_hint: str | None = None,
                    show_folder_origin: bool = False) -> LoadResult:
        self._empty_text = empty_text
        self._empty_hint = empty_hint
        self._gallery_model.set_show_folder_origin(show_folder_origin)
        valid_rows = [r for r in rows if os.path.isfile(r["path"])]
        filtered = self._apply_rating_filter(valid_rows)
        shown = len(filtered)
        sfw_hidden = len(valid_rows) - len(filtered)
        missing = len(rows) - len(valid_rows)
        self._load_rows(filtered)
        return LoadResult(shown, sfw_hidden, missing)

    def load_paths(self, paths: list[str]):
        self._gallery_model.set_show_folder_origin(False)
        valid = [p for p in paths if os.path.isfile(p)]
        rows, _recovered = db.get_or_create_images_batch(valid)
        self._load_rows(rows)

    def _on_double_click(self, index: QModelIndex):
        image_id = self._gallery_model.get_image_id(index.row())
        if image_id is not None:
            self.image_double_clicked.emit(image_id)

    def get_selected_ids(self) -> list[int]:
        ids = []
        for index in self.selectedIndexes():
            image_id = self._gallery_model.get_image_id(index.row())
            if image_id is not None:
                ids.append(image_id)
        return ids

    def _on_context_menu(self, pos):
        ids = self.get_selected_ids()
        if ids:
            self.context_menu_requested.emit(ids, self.viewport().mapToGlobal(pos))
        else:
            self.empty_context_menu_requested.emit(self.viewport().mapToGlobal(pos))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._gallery_model.rowCount() == 0 and not self._loading:
            painter = QPainter(self.viewport())
            rect = self.viewport().rect()
            cx, cy = rect.center().x(), rect.center().y()

            # Folder icon centered above text
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            icon_size = 48
            icon.paint(painter, QRect(cx - icon_size // 2, cy - icon_size - 28, icon_size, icon_size))

            # Primary text
            font = painter.font()
            font.setPointSize(13)
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            painter.setPen(QColor("#444444"))
            painter.drawText(QRect(rect.x(), cy - 8, rect.width(), 28),
                             Qt.AlignmentFlag.AlignHCenter, self._empty_text)

            # Secondary hint
            font.setPointSize(10)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor("#888888"))
            if self._empty_hint is not None:
                hint = self._empty_hint
            else:
                hint = "Open a folder via File > Open Folder"
            painter.drawText(QRect(rect.x(), cy + 24, rect.width(), 22),
                             Qt.AlignmentFlag.AlignHCenter, hint)

    def image_count(self) -> int:
        return self._gallery_model.count()

    def get_all_items(self) -> list[tuple[int, str]]:
        if self._pager:
            return self._pager.all_items()
        return self._gallery_model.get_all_items()

    def remove_image(self, image_id: int):
        if self._pager:
            self._pager.remove(image_id)
            # Try to remove from model first (it may be on the current page)
            self._gallery_model.remove_image(image_id)
            self._refresh_size()
            # Update pagination controls
            self.page_changed.emit(
                self._pager.current_page,
                self._pager.page_count,
                self._pager.total,
            )
        else:
            self._gallery_model.remove_image(image_id)
            self._refresh_size()

    def mark_image_error(self, image_id: int):
        """Mark a thumbnail with a red error overlay (failed file operation)."""
        self._gallery_model.mark_error(image_id)
