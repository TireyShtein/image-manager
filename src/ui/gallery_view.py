import os
from PyQt6.QtWidgets import QListView, QAbstractItemView
from PyQt6.QtCore import (Qt, QAbstractListModel, QModelIndex, QSize,
                           QRunnable, QThreadPool, pyqtSignal, QObject, pyqtSlot)
from PyQt6.QtGui import QPixmap
from src.core import thumbnail_cache, database as db

# Maximum size stored in the thumbnail cache on disk
CACHE_THUMB_SIZE = 256

# Size tiers: (max_count, thumb_px)
_SIZE_TIERS = [
    (4,    300),
    (12,   240),
    (30,   180),
    (80,   140),
    (200,  110),
    (500,   85),
]
_MIN_THUMB_SIZE = 70


def _compute_thumb_size(count: int) -> int:
    for max_count, size in _SIZE_TIERS:
        if count <= max_count:
            return size
    return _MIN_THUMB_SIZE


class ThumbnailSignals(QObject):
    loaded = pyqtSignal(int, str)  # (image_id, thumb_path)


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

    def set_images(self, rows):
        self.beginResetModel()
        self._items = [{"id": r["id"], "path": r["path"],
                        "source_pix": None, "display_pix": None} for r in rows]
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self.endResetModel()
        self._start_loading()

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

    def _start_loading(self):
        for item in self._items:
            loader = ThumbnailLoader(item["id"], item["path"], self._signals)
            self._pool.start(loader)

    def _on_thumbnail_loaded(self, image_id: int, thumb_path: str):
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        source = QPixmap(thumb_path)
        self._items[idx]["source_pix"] = source
        self._items[idx]["display_pix"] = self._scale(source)
        index = self.index(idx)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            return item["display_pix"] or QPixmap(self._display_size, self._display_size)
        if role == Qt.ItemDataRole.DisplayRole:
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
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self.endRemoveRows()

    def count(self) -> int:
        return len(self._items)


class GalleryView(QListView):
    image_double_clicked = pyqtSignal(int)
    selection_changed = pyqtSignal(list)
    context_menu_requested = pyqtSignal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gallery_model = GalleryModel(self)
        self.setModel(self._gallery_model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self._apply_size(_compute_thumb_size(0))
        self.doubleClicked.connect(self._on_double_click)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _apply_size(self, thumb_px: int):
        padding = 24  # room for filename label below thumb
        self.setIconSize(QSize(thumb_px, thumb_px))
        self.setGridSize(QSize(thumb_px + 8, thumb_px + padding))
        self._gallery_model.set_display_size(thumb_px)

    def _refresh_size(self):
        count = self._gallery_model.count()
        self._apply_size(_compute_thumb_size(count))

    def _load_rows(self, rows):
        self._gallery_model.set_images(rows)
        self._refresh_size()

    def load_folder(self, folder: str):
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
        rows = []
        for path in sorted(paths, key=lambda p: os.path.basename(p).lower()):
            row = db.get_image_by_path(path)
            if not row:
                image_id = db.add_image(path, os.path.basename(path))
                row = db.get_image(image_id)
            if row:
                rows.append(row)
        self._load_rows(rows)

    def load_images(self, rows):
        self._load_rows(rows)

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

    def remove_image(self, image_id: int):
        self._gallery_model.remove_image(image_id)
        self._refresh_size()
