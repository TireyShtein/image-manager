from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QPushButton, QLineEdit, QLabel,
                              QInputDialog, QMessageBox)
from PyQt6.QtCore import pyqtSignal, Qt
from src.core import database as db


class AlbumPanel(QWidget):
    album_selected = pyqtSignal(int)   # album_id to filter gallery

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_image_ids: list[int] = []
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("<b>Albums</b>"))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_album_double_clicked)
        layout.addWidget(self._list)

        row = QHBoxLayout()
        self._album_input = QLineEdit()
        self._album_input.setPlaceholderText("New album...")
        self._album_input.returnPressed.connect(self._create_album)
        row.addWidget(self._album_input)

        btn_add = QPushButton("+")
        btn_add.setFixedWidth(28)
        btn_add.clicked.connect(self._create_album)
        row.addWidget(btn_add)

        layout.addLayout(row)

        btn_add_imgs = QPushButton("Add selected to album")
        btn_add_imgs.clicked.connect(self._add_images_to_album)
        layout.addWidget(btn_add_imgs)

        btn_remove_imgs = QPushButton("Remove selected from album")
        btn_remove_imgs.clicked.connect(self._remove_images_from_album)
        layout.addWidget(btn_remove_imgs)

        btn_rename = QPushButton("Rename album")
        btn_rename.clicked.connect(self._rename_album)
        layout.addWidget(btn_rename)

        btn_delete = QPushButton("Delete album")
        btn_delete.clicked.connect(self._delete_album)
        layout.addWidget(btn_delete)

    def set_selected_images(self, image_ids: list[int]):
        self._selected_image_ids = image_ids

    def refresh(self):
        self._list.clear()
        for row in db.get_all_albums():
            count = db.get_album_image_count(row["id"])
            item = QListWidgetItem(f"{row['name']} ({count})")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._list.addItem(item)

    def _create_album(self):
        name = self._album_input.text().strip()
        if not name:
            return
        db.create_album(name)
        self._album_input.clear()
        self.refresh()

    def _add_images_to_album(self):
        item = self._list.currentItem()
        if not item or not self._selected_image_ids:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        for image_id in self._selected_image_ids:
            db.add_image_to_album(album_id, image_id)
        self.refresh()

    def _remove_images_from_album(self):
        item = self._list.currentItem()
        if not item or not self._selected_image_ids:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        for image_id in self._selected_image_ids:
            db.remove_image_from_album(album_id, image_id)
        self.refresh()

    def _rename_album(self):
        item = self._list.currentItem()
        if not item:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        current_name = item.text().split(" (")[0]
        new_name, ok = QInputDialog.getText(self, "Rename Album", "New name:", text=current_name)
        if ok and new_name.strip() and new_name.strip() != current_name:
            db.rename_album(album_id, new_name.strip())
            self.refresh()

    def _delete_album(self):
        item = self._list.currentItem()
        if not item:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        album_name = item.text().split(" (")[0]
        reply = QMessageBox.question(self, "Delete Album",
                                     f"Delete album '{album_name}'? Images will not be deleted.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_album(album_id)
            self.refresh()

    def _on_album_double_clicked(self, item: QListWidgetItem):
        album_id = item.data(Qt.ItemDataRole.UserRole)
        self.album_selected.emit(album_id)
