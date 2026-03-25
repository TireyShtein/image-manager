import os
from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, QThreadPool
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen
from src.ui.gallery.constants import _compute_thumb_size, _get_placeholder, _PREFETCH_MARGIN, _EVICT_MARGIN
from src.ui.gallery.workers import ThumbnailLoader, ThumbnailSignals


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
        new_items = [{"id": r["id"], "path": r["path"], "display_pix": None} for r in rows]
        # Use insertRows when the model is empty — avoids the full reset signal
        # cascade (persistent index invalidation, selection clear, full repaint).
        # Full reset is still required when replacing an existing page of rows.
        append_only = not self._items and bool(new_items)
        if append_only:
            self.beginInsertRows(QModelIndex(), 0, len(new_items) - 1)
        else:
            self.beginResetModel()
        self._items = new_items
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self._total = len(self._items)
        self._loaded = 0
        self._queued = set()
        self._error_ids = set()
        self._thumb_token += 1
        if append_only:
            self.endInsertRows()
        else:
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

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                Qt.ItemFlag.ItemIsDragEnabled)

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

    def get_item(self, row: int) -> dict | None:
        if 0 <= row < len(self._items):
            return self._items[row]
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
