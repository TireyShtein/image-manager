from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QPushButton, QLineEdit, QLabel,
                              QFrame, QInputDialog, QMessageBox, QCompleter)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont, QColor
from src.core import database as db

LIST_STYLE = (
    "QListWidget { outline: 0; }"
    "QListWidget::item:hover { background: rgba(100, 150, 255, 0.10); }"
    "QListWidget::item:selected { background: rgba(80, 130, 255, 0.22); color: palette(text); }"
)


class TagPanel(QWidget):
    tag_filter_changed = pyqtSignal(str)   # tag name or "" to clear filter

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_image_ids: list[int] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 4)
        layout.setSpacing(4)

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
        layout.addSpacing(4)
        all_tags_label = QLabel("All Tags")
        font = all_tags_label.font()
        font.setPointSize(font.pointSize() + 1)
        font.setWeight(QFont.Weight.DemiBold)
        all_tags_label.setFont(font)
        all_tags_label.setStyleSheet("color: #ffffff;")
        layout.addWidget(all_tags_label)

        self._global_list = QListWidget()
        self._global_list.setStyleSheet(LIST_STYLE)
        self._global_list.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self._global_list)

        self._btn_clear = QPushButton("Clear filter")
        self._btn_clear.setObjectName("btn_clear")
        self._btn_clear.setStyleSheet(
            "QPushButton#btn_clear { color: #fff; border: 1px solid rgba(255,255,255,0.30);"
            " background: transparent; border-radius: 3px; padding: 2px 6px; }"
            "QPushButton#btn_clear:hover { background: rgba(255,255,255,0.09); }"
            "QPushButton#btn_clear:disabled { color: rgba(255,255,255,0.30);"
            " border-color: rgba(255,255,255,0.15); }"
        )
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._clear_filter)
        layout.addWidget(self._btn_clear)

        # --- Separator ---
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: rgba(255,255,255,0.15);")
        layout.addSpacing(4)
        layout.addWidget(separator)
        layout.addSpacing(4)

        # --- Selected image tags list ---
        self._selection_label = QLabel("Selected Image Tags")
        font2 = self._selection_label.font()
        font2.setPointSize(font2.pointSize() + 1)
        font2.setWeight(QFont.Weight.DemiBold)
        self._selection_label.setFont(font2)
        self._selection_label.setStyleSheet("color: #ffffff;")
        layout.addWidget(self._selection_label)

        self._selected_list = QListWidget()
        self._selected_list.setStyleSheet(LIST_STYLE)
        self._selected_list.currentItemChanged.connect(self._on_selected_list_item_changed)
        layout.addWidget(self._selected_list)

        self._btn_remove = QPushButton("Remove tag from selected")
        self._btn_remove.setStyleSheet(
            "QPushButton:disabled { color: rgba(255,255,255,0.30);"
            " border-color: rgba(255,255,255,0.15); }"
        )
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._remove_tag)
        layout.addWidget(self._btn_remove)

    def clear_search(self):
        """Called by MainWindow on folder navigation to reset search state."""
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._global_list.clearSelection()
        self._btn_clear.setEnabled(False)
        self.refresh()

    def _clear_filter(self):
        self._global_list.clearSelection()
        self._selected_list.clearSelection()
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._btn_clear.setEnabled(False)
        self.tag_filter_changed.emit("")

    def _on_selected_list_item_changed(self, current, previous):
        self._btn_remove.setEnabled(current is not None and bool(self._selected_image_ids))

    def set_selected_images(self, image_ids: list[int]):
        self._selected_image_ids = image_ids
        n = len(image_ids)
        if n > 1:
            self._btn_remove.setText(f"Remove tag from all {n} images")
        else:
            self._btn_remove.setText("Remove tag from selected")
        self._btn_remove.setEnabled(False)
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
            suffix = f" — {n} images" if n > 1 else ""
            self._selection_label.setText(f"Selected Image Tags{suffix}")
            tag_rows = db.get_tags_for_images(self._selected_image_ids)
            for row in tag_rows:
                name, count = row["name"], row["count"]
                if query_lower and query_lower not in name.lower():
                    continue
                label = name if count == n else f"{name} ({count}/{n})"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip(name)
                if count < n:
                    item.setForeground(QColor(255, 255, 255, 140))  # ~55% opacity
                self._selected_list.addItem(item)
        else:
            self._selection_label.setText("Selected Image Tags")

    def _remove_tag(self):
        item = self._selected_list.currentItem()
        if not item or not self._selected_image_ids:
            return
        tag_name = item.data(Qt.ItemDataRole.UserRole)
        for image_id in self._selected_image_ids:
            db.remove_tag_from_image(image_id, tag_name)
        self.refresh()

    def _on_tag_clicked(self, item: QListWidgetItem):
        tag_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self._btn_clear.setEnabled(True)
        self.tag_filter_changed.emit(tag_name)
