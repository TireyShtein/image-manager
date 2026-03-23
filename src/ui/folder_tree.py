import json
import os
from PyQt6.QtWidgets import QTreeView, QAbstractItemView
from PyQt6.QtCore import pyqtSignal, Qt, QDir, QItemSelectionModel, QTimer
from PyQt6.QtGui import QFileSystemModel

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.wmv', '.flv', '.m4v'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Name filters for QFileSystemModel (shows these files + all directories)
_NAME_FILTERS = [f'*{ext}' for ext in MEDIA_EXTENSIONS]

# Drag-and-drop MIME type for image ID payloads (must match gallery_view.py)
_MIME_IMAGE_IDS = "application/x-imagemanager-ids"


class FolderTree(QTreeView):
    folder_selected = pyqtSignal(str)                  # folder path clicked
    files_selected = pyqtSignal(list)                  # list of file paths selected
    images_dropped_on_folder = pyqtSignal(list, str)   # (image_ids, folder_path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = QFileSystemModel()
        self._model.setReadOnly(True)   # disables QFileSystemModel's built-in drop-to-move
        self._model.setRootPath(QDir.rootPath())
        self._model.setFilter(
            QDir.Filter.AllDirs |
            QDir.Filter.Files |
            QDir.Filter.NoDotAndDotDot
        )
        self._model.setNameFilters(_NAME_FILTERS)
        self._model.setNameFilterDisables(False)  # hide non-matching files entirely

        self.setModel(self._model)
        self.setRootIndex(self._model.index(QDir.rootPath()))

        # Hide Size / Type / Date Modified columns
        for col in range(1, self._model.columnCount()):
            self.hideColumn(col)

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAnimated(True)
        self.setHeaderHidden(True)

        self.setAcceptDrops(True)
        self._pre_drag_index = None   # saved current index before drag enters

        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double_clicked)

    def _on_clicked(self, index):
        path = self._model.filePath(index)
        if self._model.isDir(index):
            self.folder_selected.emit(path)

    def _on_double_clicked(self, index):
        if self._model.isDir(index):
            self.set_root(self._model.filePath(index))

    def _on_selection_changed(self, selected, deselected):
        paths = []
        for index in self.selectedIndexes():
            path = self._model.filePath(index)
            if not self._model.isDir(index):
                ext = os.path.splitext(path)[1].lower()
                if ext in MEDIA_EXTENSIONS:
                    paths.append(path)
        if paths:
            self.files_selected.emit(paths)

    def set_root(self, path: str):
        """Restrict the tree to show only this folder as root."""
        self._model.setRootPath(path)
        self.setRootIndex(self._model.index(path))
        self.setCurrentIndex(self._model.index(path))

    def navigate_to(self, path: str):
        index = self._model.index(path)
        self.setCurrentIndex(index)
        self.expand(index)
        self.scrollTo(index)

    def select_files(self, paths: list[str]):
        """Highlight the given file paths in the tree.

        Uses a short deferred call because QFileSystemModel populates
        directory contents asynchronously after set_root().
        Signals are blocked during selection to prevent files_selected from
        firing and overwriting the gallery view that was just loaded.
        """
        def _do_select():
            sel = self.selectionModel()
            self.blockSignals(True)
            sel.blockSignals(True)
            sel.clearSelection()
            scroll_target = None
            for path in paths:
                index = self._model.index(path)
                if index.isValid():
                    sel.select(index, QItemSelectionModel.SelectionFlag.Select)
                    if scroll_target is None:
                        scroll_target = index
            if scroll_target is not None:
                self.scrollTo(scroll_target)
            sel.blockSignals(False)
            self.blockSignals(False)
        QTimer.singleShot(100, _do_select)

    # ------------------------------------------------------------------
    # Drag-and-drop: folder drop target
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_MIME_IMAGE_IDS):
            # Save the user's current selection before drag highlight changes it
            self._pre_drag_index = self.currentIndex()
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasFormat(_MIME_IMAGE_IDS):
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and self._model.isDir(index):
            # Block signals so selectionChanged doesn't fire (which would reload
            # the gallery). ClearAndSelect gives the full blue selection highlight
            # the user recognises from normal folder clicks — much more visible
            # than NoUpdate's subtle dotted-border "current item" indicator.
            # viewport().update() is required because blockSignals also suppresses
            # Qt's internal repaint notification — the view won't redraw otherwise.
            if index != self.currentIndex():  # skip repaint if already on same row
                self.selectionModel().blockSignals(True)
                self.selectionModel().setCurrentIndex(
                    index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.selectionModel().blockSignals(False)
                self.viewport().update()
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _restore_pre_drag_selection(self):
        """Restore the selection that existed before the drag started."""
        self.selectionModel().blockSignals(True)
        if self._pre_drag_index is not None and self._pre_drag_index.isValid():
            self.selectionModel().setCurrentIndex(
                self._pre_drag_index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        else:
            self.selectionModel().clearSelection()
        self.selectionModel().blockSignals(False)
        self.viewport().update()  # repaint — blocked signals suppress Qt's own dirty notification
        self._pre_drag_index = None

    def dragLeaveEvent(self, event):
        self._restore_pre_drag_selection()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(_MIME_IMAGE_IDS):
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint())
        if not index.isValid() or not self._model.isDir(index):
            event.ignore()
            return
        folder_path = self._model.filePath(index)
        try:
            ids = json.loads(bytes(event.mimeData().data(_MIME_IMAGE_IDS)).decode())
        except (ValueError, KeyError):
            event.ignore()
            return
        self._restore_pre_drag_selection()
        event.acceptProposedAction()
        self.images_dropped_on_folder.emit(ids, folder_path)
