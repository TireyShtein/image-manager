from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QPushButton, QLineEdit, QLabel,
                              QInputDialog, QMessageBox, QCompleter)
from PyQt6.QtCore import pyqtSignal, Qt
from src.core import database as db

LIST_STYLE = (
    "QListWidget::item:hover { background: #dde8ff; }"
    "QListWidget::item:selected { background: #b8d0ff; }"
)


class TagPanel(QWidget):
    tag_filter_changed = pyqtSignal(str)   # tag name or "" to clear filter

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_image_ids: list[int] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search tags…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self.refresh)
        layout.addWidget(self._search_input)
        try:
            from src.ai.wd14_tagger import get_all_tags
            _completer = QCompleter(get_all_tags(), self)
            _completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            _completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._search_input.setCompleter(_completer)
        except Exception:
            pass

        # --- Global tags list ---
        layout.addWidget(QLabel("<b>All Tags</b> — click to filter"))
        self._global_list = QListWidget()
        self._global_list.setStyleSheet(LIST_STYLE)
        self._global_list.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self._global_list)

        btn_clear = QPushButton("Clear filter")
        btn_clear.clicked.connect(lambda: self.tag_filter_changed.emit(""))
        layout.addWidget(btn_clear)

        # --- Selected image tags list ---
        self._selection_label = QLabel("<b>Selected Image Tags</b>")
        layout.addWidget(self._selection_label)

        self._selected_list = QListWidget()
        self._selected_list.setStyleSheet(LIST_STYLE)
        self._selected_list.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self._selected_list)

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

    def set_selected_images(self, image_ids: list[int]):
        self._selected_image_ids = image_ids
        self.refresh()

    def refresh(self):
        query = self._search_input.text().strip()
        query_lower = query.lower()

        # Global tags list
        self._global_list.clear()
        rows = db.search_tags_with_counts(query) if query else db.get_all_tags_with_counts()
        for row in rows:
            item = QListWidgetItem(f"{row['name']} ({row['count']})")
            item.setData(Qt.ItemDataRole.UserRole, row["name"])
            item.setToolTip("Click to filter gallery by this tag")
            self._global_list.addItem(item)

        # Selected image tags list
        self._selected_list.clear()
        if self._selected_image_ids:
            n = len(self._selected_image_ids)
            label = f"<b>Selected Image Tags</b>" + (f" — image 1 of {n}" if n > 1 else "")
            self._selection_label.setText(label)
            tags = db.get_tags_for_image(self._selected_image_ids[0])
            for name in tags:
                if query_lower and query_lower not in name.lower():
                    continue
                item = QListWidgetItem(name)
                item.setToolTip("Click to filter gallery by this tag")
                self._selected_list.addItem(item)
        else:
            self._selection_label.setText("<b>Selected Image Tags</b>")

    def _add_tag(self):
        name = self._tag_input.text().strip()
        if not name or not self._selected_image_ids:
            return
        for image_id in self._selected_image_ids:
            db.add_tag_to_image(image_id, name)
        self._tag_input.clear()
        self.refresh()

    def _remove_tag(self):
        item = self._selected_list.currentItem()
        if not item or not self._selected_image_ids:
            return
        tag_name = item.text()
        for image_id in self._selected_image_ids:
            db.remove_tag_from_image(image_id, tag_name)
        self.refresh()

    def _on_tag_clicked(self, item: QListWidgetItem):
        tag_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self.tag_filter_changed.emit(tag_name)
