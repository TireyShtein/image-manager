import os
from PyQt6.QtWidgets import QListView, QAbstractItemView, QStyle
from PyQt6.QtCore import (Qt, QAbstractListModel, QModelIndex, QSize, QRect,
                           QRunnable, QThreadPool, pyqtSignal, QObject, pyqtSlot,
                           QTimer, QPoint)
from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor
from src.core import thumbnail_cache, database as db

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


class ThumbnailSignals(QObject):
    loaded = pyqtSignal(int, str)   # (image_id, thumb_path)
    progress = pyqtSignal(int, int) # (loaded_count, total_count)
    all_loaded = pyqtSignal()


class ThumbnailLoader(QRunnable):
    def __init__(self, image_id: int, image_path: str, signals: ThumbnailSignals):
        super().__init__()
        self.image_id = image_id
        self.image_path = image_path
        self.signals = signals
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        thumb = thumbnail_cache.get_or_create_thumbnail(self.image_path)
        if thumb:
            self.signals.loaded.emit(self.image_id, thumb)


class GalleryModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Each item: {"id", "path", "source_pix": QPixmap|None, "display_pix": QPixmap|None}
        self._items: list[dict] = []
        self._id_index: dict[int, int] = {}
        self._display_size: int = _compute_thumb_size(0)
        self._pool = QThreadPool.globalInstance()
        self._signals = ThumbnailSignals()
        self._signals.loaded.connect(self._on_thumbnail_loaded)
        self._total: int = 0
        self._loaded: int = 0
        # Track which rows have been queued for thumbnail loading
        self._queued: set[int] = set()

    def set_images(self, rows):
        self.beginResetModel()
        self._items = [{"id": r["id"], "path": r["path"],
                        "source_pix": None, "display_pix": None} for r in rows]
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self._total = len(self._items)
        self._loaded = 0
        self._queued = set()
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
        # Re-scale all already-loaded pixmaps
        for item in self._items:
            if item["source_pix"] is not None:
                item["display_pix"] = self._scale(item["source_pix"])
        if self._items:
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

        Only queues items that have no source_pix and are not already queued.
        """
        start = max(0, first_row - _PREFETCH_MARGIN)
        end = min(len(self._items), last_row + _PREFETCH_MARGIN + 1)
        for i in range(start, end):
            if i in self._queued:
                continue
            item = self._items[i]
            if item["source_pix"] is not None:
                continue
            self._queued.add(i)
            loader = ThumbnailLoader(item["id"], item["path"], self._signals)
            self._pool.start(loader)

    def _evict_offscreen(self, first_visible: int, last_visible: int):
        """Free pixmap memory for rows far outside the visible range."""
        keep_start = max(0, first_visible - _EVICT_MARGIN)
        keep_end = min(len(self._items), last_visible + _EVICT_MARGIN + 1)
        for i in range(0, keep_start):
            item = self._items[i]
            if item["source_pix"] is not None:
                item["source_pix"] = None
                item["display_pix"] = None
                self._queued.discard(i)
        for i in range(keep_end, len(self._items)):
            item = self._items[i]
            if item["source_pix"] is not None:
                item["source_pix"] = None
                item["display_pix"] = None
                self._queued.discard(i)

    def _on_thumbnail_loaded(self, image_id: int, thumb_path: str):
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        # If the item was evicted before the loader finished, discard the result
        if idx not in self._queued:
            return
        source = QPixmap(thumb_path)
        self._items[idx]["source_pix"] = source
        self._items[idx]["display_pix"] = self._scale(source)
        index = self.index(idx)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
        self._loaded += 1
        self._signals.progress.emit(self._loaded, self._total)
        if self._loaded == self._total:
            self._signals.all_loaded.emit()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            if item["display_pix"] is not None:
                return item["display_pix"]
            return _get_placeholder(self._display_size)
        if role == Qt.ItemDataRole.DisplayRole:
            if self._display_size < 140:
                return None
            return os.path.basename(item["path"])
        if role == Qt.ItemDataRole.ToolTipRole:
            return item["path"]
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

    def count(self) -> int:
        return len(self._items)


class GalleryView(QListView):
    image_double_clicked = pyqtSignal(int)
    selection_changed = pyqtSignal(list)
    context_menu_requested = pyqtSignal(list, object)
    empty_context_menu_requested = pyqtSignal(object)  # global pos
    thumbnails_loading = pyqtSignal(int, int)  # (loaded, total)
    thumbnails_ready = pyqtSignal(int)          # total count

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gallery_model = GalleryModel(self)
        self._empty_text = "No media in this folder"
        self._loading = False
        self._excluded_rating_tags: list[str] = []
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
        self._gallery_model._signals.progress.connect(self.thumbnails_loading)
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

    def _refresh_size(self):
        count = self._gallery_model.count()
        self._apply_size(_compute_thumb_size(count))

    def _load_rows(self, rows):
        self.clearSelection()
        # For non-empty folders, clear _loading immediately so the empty-state
        # overlay doesn't flash.  With lazy loading there is no single
        # "all thumbnails done" moment to wait for before clearing.
        self._loading = False
        self._apply_size(_compute_thumb_size(len(rows)))
        self._gallery_model.set_images(rows)
        # Kick off initial visible-range loading after the model is populated
        # and Qt has had a chance to lay out items.
        if len(rows) > 0:
            QTimer.singleShot(0, self._on_scroll)

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
        from src.core.thumbnail_cache import VIDEO_EXTENSIONS
        from src.core.image_scanner import SUPPORTED_EXTENSIONS
        media_exts = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS
        paths = []
        try:
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and os.path.splitext(name)[1].lower() in media_exts:
                    paths.append(full)
        except PermissionError:
            pass
        sorted_paths = sorted(paths, key=lambda p: os.path.basename(p).lower())
        rows = db.get_or_create_images_batch(sorted_paths)
        self._load_rows(self._apply_rating_filter(rows))

    def load_images(self, rows, empty_text: str = "No images match this filter") -> int:
        self._empty_text = empty_text
        valid_rows = [r for r in rows if os.path.isfile(r["path"])]
        filtered = self._apply_rating_filter(valid_rows)
        self._load_rows(filtered)
        return len(filtered)

    def load_paths(self, paths: list[str]):
        rows = []
        for path in paths:
            if not os.path.isfile(path):
                continue
            row = db.get_image_by_path(path)
            if not row:
                image_id = db.add_image(path, os.path.basename(path))
                row = db.get_image(image_id)
            if row:
                rows.append(row)
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
            painter.drawText(QRect(rect.x(), cy + 24, rect.width(), 22),
                             Qt.AlignmentFlag.AlignHCenter,
                             "Open a folder via File \u203a Open Folder")

    def image_count(self) -> int:
        return self._gallery_model.count()

    def get_all_items(self) -> list[tuple[int, str]]:
        return self._gallery_model.get_all_items()

    def remove_image(self, image_id: int):
        self._gallery_model.remove_image(image_id)
        self._refresh_size()
