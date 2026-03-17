import os
from PyQt6.QtWidgets import QTreeView, QAbstractItemView
from PyQt6.QtCore import pyqtSignal, QDir, QItemSelectionModel
from PyQt6.QtGui import QFileSystemModel

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.wmv', '.flv', '.m4v'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Name filters for QFileSystemModel (shows these files + all directories)
_NAME_FILTERS = [f'*{ext}' for ext in MEDIA_EXTENSIONS]


class FolderTree(QTreeView):
    folder_selected = pyqtSignal(str)          # folder path clicked
    files_selected = pyqtSignal(list)          # list of file paths selected

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = QFileSystemModel()
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

        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self, index):
        path = self._model.filePath(index)
        if self._model.isDir(index):
            self.folder_selected.emit(path)

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
