from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QPushButton, QLineEdit, QLabel,
                              QInputDialog, QMessageBox)
from PyQt6.QtCore import pyqtSignal, Qt
from src.core import database as db


class TagPanel(QWidget):
    tag_filter_changed = pyqtSignal(str)   # tag name or "" to clear filter

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_image_ids: list[int] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("<b>Tags</b> — click to filter"))

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget::item:hover { background: #dde8ff; }"
            "QListWidget::item:selected { background: #b8d0ff; }"
        )
        self._list.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self._list)

        row = QHBoxLayout()
        self._tag_input = QLineEdit()
        self._tag_input.setPlaceholderText("New tag...")
        self._tag_input.returnPressed.connect(self._add_tag)
        row.addWidget(self._tag_input)

        btn_add = QPushButton("+")
        btn_add.setFixedWidth(28)
        btn_add.clicked.connect(self._add_tag)
        row.addWidget(btn_add)

        layout.addLayout(row)

        btn_remove = QPushButton("Remove tag from selected")
        btn_remove.clicked.connect(self._remove_tag)
        layout.addWidget(btn_remove)

        btn_clear = QPushButton("Clear filter")
        btn_clear.clicked.connect(lambda: self.tag_filter_changed.emit(""))
        layout.addWidget(btn_clear)

    def set_selected_images(self, image_ids: list[int]):
        self._selected_image_ids = image_ids
        self.refresh()

    def refresh(self):
        self._list.clear()
        if not self._selected_image_ids:
            # Show all tags when nothing selected
            for row in db.get_all_tags():
                item = QListWidgetItem(row["name"])
                item.setToolTip("Click to filter gallery by this tag")
                self._list.addItem(item)
        else:
            # Show tags for the first selected image
            tags = db.get_tags_for_image(self._selected_image_ids[0])
            for name in tags:
                item = QListWidgetItem(name)
                item.setToolTip("Click to filter gallery by this tag")
                self._list.addItem(item)

    def _add_tag(self):
        name = self._tag_input.text().strip()
        if not name or not self._selected_image_ids:
            return
        for image_id in self._selected_image_ids:
            db.add_tag_to_image(image_id, name)
        self._tag_input.clear()
        self.refresh()

    def _remove_tag(self):
        item = self._list.currentItem()
        if not item or not self._selected_image_ids:
            return
        tag_name = item.text()
        for image_id in self._selected_image_ids:
            db.remove_tag_from_image(image_id, tag_name)
        self.refresh()

    def _on_tag_clicked(self, item: QListWidgetItem):
        self.tag_filter_changed.emit(item.text())
