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

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search tags…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self.refresh)
        layout.addWidget(self._search_input)

        self._selection_label = QLabel("")
        self._selection_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._selection_label)

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
        if image_ids:
            self._search_input.blockSignals(True)
            self._search_input.clear()
            self._search_input.blockSignals(False)
        self.refresh()

    def refresh(self):
        self._list.clear()
        if not self._selected_image_ids:
            self._selection_label.setText("")
            query = self._search_input.text().strip()
            rows = db.search_tags_with_counts(query) if query else db.get_all_tags_with_counts()
            for row in rows:
                item = QListWidgetItem(f"{row['name']} ({row['count']})")
                item.setData(Qt.ItemDataRole.UserRole, row["name"])
                item.setToolTip("Click to filter gallery by this tag")
                self._list.addItem(item)
        else:
            n = len(self._selected_image_ids)
            self._selection_label.setText(
                f"Showing tags for image 1 of {n} selected" if n > 1 else ""
            )
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
        tag_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        for image_id in self._selected_image_ids:
            db.remove_tag_from_image(image_id, tag_name)
        self.refresh()

    def _on_tag_clicked(self, item: QListWidgetItem):
        tag_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self.tag_filter_changed.emit(tag_name)
