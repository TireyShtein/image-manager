from PyQt6.QtWidgets import QTreeView, QAbstractItemView
from PyQt6.QtCore import pyqtSignal, QDir
from PyQt6.QtGui import QFileSystemModel


class FolderTree(QTreeView):
    folder_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = QFileSystemModel()
        self._model.setRootPath(QDir.rootPath())
        self._model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)

        self.setModel(self._model)
        self.setRootIndex(self._model.index(QDir.rootPath()))

        # Hide all columns except Name
        for col in range(1, self._model.columnCount()):
            self.hideColumn(col)

        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAnimated(True)
        self.setHeaderHidden(True)
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self, index):
        path = self._model.filePath(index)
        self.folder_selected.emit(path)

    def navigate_to(self, path: str):
        index = self._model.index(path)
        self.setCurrentIndex(index)
        self.expand(index)
        self.scrollTo(index)
