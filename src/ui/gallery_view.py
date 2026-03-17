import os
from PyQt6.QtWidgets import QListView, QAbstractItemView, QMenu
from PyQt6.QtCore import (Qt, QAbstractListModel, QModelIndex, QSize,
                           QRunnable, QThreadPool, pyqtSignal, QObject, pyqtSlot)
from PyQt6.QtGui import QPixmap, QIcon
from src.core import thumbnail_cache, database as db

THUMB_SIZE = 160
ITEM_SIZE = 180


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
        self._items: list[dict] = []   # {"id": int, "path": str, "pixmap": QPixmap|None}
        self._id_index: dict[int, int] = {}
        self._pool = QThreadPool.globalInstance()
        self._signals = ThumbnailSignals()
        self._signals.loaded.connect(self._on_thumbnail_loaded)

    def set_images(self, rows):
        self.beginResetModel()
        self._items = [{"id": r["id"], "path": r["path"], "pixmap": None} for r in rows]
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self.endResetModel()
        self._start_loading()

    def _start_loading(self):
        for item in self._items:
            loader = ThumbnailLoader(item["id"], item["path"], self._signals)
            self._pool.start(loader)

    def _on_thumbnail_loaded(self, image_id: int, thumb_path: str):
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        pix = QPixmap(thumb_path).scaled(
            THUMB_SIZE, THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self._items[idx]["pixmap"] = pix
        index = self.index(idx)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            if item["pixmap"]:
                return item["pixmap"]
            return QPixmap(THUMB_SIZE, THUMB_SIZE)  # placeholder
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
        # Rebuild index
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self.endRemoveRows()


class GalleryView(QListView):
    image_double_clicked = pyqtSignal(int)   # image_id
    selection_changed = pyqtSignal(list)     # list of image_ids
    context_menu_requested = pyqtSignal(list, object)  # image_ids, QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gallery_model = GalleryModel(self)
        self.setModel(self._gallery_model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setGridSize(QSize(ITEM_SIZE, ITEM_SIZE + 20))
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.doubleClicked.connect(self._on_double_click)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def load_folder(self, folder: str):
        rows = db.get_images_in_folder(folder)
        self._gallery_model.set_images(rows)

    def load_images(self, rows):
        self._gallery_model.set_images(rows)

    def load_paths(self, paths: list[str]):
        """Load raw file paths directly (auto-registers in DB if not present)."""
        import os
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
        self._gallery_model.set_images(rows)

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

    def selectionModel(self):
        sm = super().selectionModel()
        return sm

    def _on_context_menu(self, pos):
        ids = self.get_selected_ids()
        if ids:
            self.context_menu_requested.emit(ids, self.viewport().mapToGlobal(pos))

    def remove_image(self, image_id: int):
        self._gallery_model.remove_image(image_id)
