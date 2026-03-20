from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                             QListWidgetItem, QPushButton, QLineEdit, QLabel,
                             QFrame, QInputDialog, QMessageBox, QCompleter,
                             QAbstractItemView, QToolTip)
from PyQt6.QtCore import pyqtSignal, Qt, QStringListModel, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush, QCursor
from src.core import database as db

LIST_STYLE = (
    "QListWidget { outline: 0; }"
    "QListWidget::item:hover { background: rgba(100, 150, 255, 0.10); }"
    "QListWidget::item:selected { background: rgba(80, 130, 255, 0.22); color: palette(text); }"
)

_CTRL_BTN_STYLE = (
    "QPushButton { color:#ccc; border:1px solid rgba(255,255,255,0.20);"
    " background:transparent; border-radius:3px; padding:1px 6px; font-size:11px; }"
    "QPushButton:checked { background:rgba(80,130,255,0.25); color:#fff;"
    " border-color:rgba(80,130,255,0.6); }"
    "QPushButton:hover { background:rgba(255,255,255,0.07); }"
)

_CATEGORY_COLOR = {
    "rating":  QColor(0xf5, 0xa6, 0x23),   # amber
    "general": QColor(0xb4, 0xc7, 0xd9),   # muted blue-gray
}
_CATEGORY_LABEL = {"rating": "Rating", "general": "General"}


_SFW_BLOCKED_TAGS = {"rating:explicit", "rating:questionable"}


def _tag_category(name: str) -> str:
    return "rating" if name.startswith("rating:") else "general"


class TagPanel(QWidget):
    tag_filter_changed = pyqtSignal(list, str)  # (tag_names, mode) where mode="AND"|"OR"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_image_ids: list[int] = []
        self._active_filter_tags: set[str] = set()
        self._sort_by_count: bool = True   # True=count desc, False=alpha
        self._filter_mode: str = "AND"
        self._sfw_mode: bool = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 4)
        layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search tags…")
        self._search_input.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._refresh_global_list)
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())
        layout.addWidget(self._search_input)
        self._completer_model = QStringListModel(self)
        _completer = QCompleter(self._completer_model, self)
        _completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        _completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._search_input.setCompleter(_completer)

        # --- AND/OR + sort control row ---
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)

        self._mode_btn = QPushButton("AND")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setFixedHeight(22)
        self._mode_btn.setStyleSheet(_CTRL_BTN_STYLE)
        self._mode_btn.setToolTip(
            "AND: images must have all selected tags\n"
            "OR: images may have any selected tag"
        )
        self._mode_btn.toggled.connect(self._on_mode_toggled)
        ctrl_row.addWidget(self._mode_btn)

        self._sort_btn = QPushButton("Count ↓")
        self._sort_btn.setCheckable(True)
        self._sort_btn.setFixedHeight(22)
        self._sort_btn.setStyleSheet(_CTRL_BTN_STYLE)
        self._sort_btn.setToolTip("Toggle sort: by count or alphabetical")
        self._sort_btn.toggled.connect(self._on_sort_toggled)
        ctrl_row.addWidget(self._sort_btn)

        layout.addLayout(ctrl_row)

        # --- Global tags list ---
        layout.addSpacing(2)
        top_sep = QFrame()
        top_sep.setFrameShape(QFrame.Shape.HLine)
        top_sep.setStyleSheet("color: rgba(255,255,255,0.15);")
        layout.addWidget(top_sep)

        self._global_list = QListWidget()
        self._global_list.setStyleSheet(LIST_STYLE)
        self._global_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._global_list.itemChanged.connect(self._on_item_changed)
        self._global_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._global_list)

        self._btn_clear = QPushButton("Clear filters")
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

    def set_sfw_mode(self, enabled: bool):
        """Called by MainWindow when SFW Mode is toggled."""
        self._sfw_mode = enabled
        if enabled:
            removed = self._active_filter_tags & _SFW_BLOCKED_TAGS
            if removed:
                self._active_filter_tags -= _SFW_BLOCKED_TAGS
                self.tag_filter_changed.emit(sorted(self._active_filter_tags), self._filter_mode)
        self._refresh_global_list()

    def _on_item_clicked(self, item: QListWidgetItem):
        tag_name = item.data(Qt.ItemDataRole.UserRole)
        if self._sfw_mode and tag_name in _SFW_BLOCKED_TAGS:
            QToolTip.showText(
                QCursor.pos(),
                "SFW Mode is active — this tag is hidden from the gallery.\n"
                "Disable SFW Mode in View \u2192 SFW Mode to use this filter.",
                self._global_list,
            )

    def clear_search(self):
        """Called by MainWindow on folder navigation to reset search state."""
        self._active_filter_tags.clear()
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        # Reset AND/OR mode to default so it doesn't bleed across folder navigations
        self._filter_mode = "AND"
        self._mode_btn.blockSignals(True)
        self._mode_btn.setChecked(False)
        self._mode_btn.setText("AND")
        self._mode_btn.blockSignals(False)
        self._btn_clear.setText("Clear filters")
        self._btn_clear.setEnabled(False)
        self.refresh()

    def remove_filter_tag(self, name: str):
        """Remove a single tag from the active filter. Called by the chip bar."""
        if name not in self._active_filter_tags:
            return
        self._active_filter_tags.discard(name)
        self._refresh_global_list()
        self.tag_filter_changed.emit(sorted(self._active_filter_tags), self._filter_mode)

    def _clear_filter(self):
        self._active_filter_tags.clear()
        self._btn_clear.setText("Clear filters")
        self._btn_clear.setEnabled(False)
        self._refresh_global_list()
        self.tag_filter_changed.emit([], self._filter_mode)

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
        self._refresh_selected_tags()

    def refresh(self):
        """Full refresh — rebuilds both the global tag list and the selected image tags."""
        self._refresh_global_list()
        self._refresh_selected_tags()

    def _refresh_global_list(self):
        """Rebuild the All Tags list. Called on search, sort, folder nav, tag add/remove."""
        query = self._search_input.text().strip()

        self._global_list.blockSignals(True)
        self._global_list.clear()

        all_rows = db.get_all_tags_with_counts()
        self._completer_model.setStringList([r["name"] for r in all_rows])
        rows = db.search_tags_with_counts(query) if query else all_rows

        if self._sort_by_count:
            rows = sorted(rows, key=lambda r: r["count"], reverse=True)
        else:
            rows = sorted(rows, key=lambda r: r["name"].lower())

        groups: dict[str, list] = {"rating": [], "general": []}
        for row in rows:
            groups[_tag_category(row["name"])].append(row)

        for cat_key in ("rating", "general"):
            cat_rows = groups[cat_key]
            if not cat_rows:
                continue

            header = QListWidgetItem(f"  {_CATEGORY_LABEL[cat_key]}  ({len(cat_rows)})")
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header.setForeground(QColor(160, 160, 160))
            f = header.font()
            f.setWeight(QFont.Weight.DemiBold)
            f.setPointSize(f.pointSize() - 1)
            header.setFont(f)
            header.setBackground(QColor(40, 40, 40))
            self._global_list.addItem(header)

            cat_color = _CATEGORY_COLOR[cat_key]
            for row in cat_rows:
                name, count = row["name"], row["count"]
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, name)

                sfw_blocked = self._sfw_mode and name in _SFW_BLOCKED_TAGS
                if sfw_blocked:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    item.setText(f"  {name}  ({count})  \u00d8")
                    item.setForeground(QColor(90, 60, 20))
                    item.setToolTip(
                        f"{name}\nSFW Mode is active — disable in View \u2192 SFW Mode."
                    )
                else:
                    item.setToolTip(f"{name}\nClick to toggle filter")
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled |
                        Qt.ItemFlag.ItemIsUserCheckable
                    )
                    is_active = name in self._active_filter_tags
                    item.setCheckState(
                        Qt.CheckState.Checked if is_active else Qt.CheckState.Unchecked
                    )
                    item.setText(f"  {name}  ({count})")
                    if is_active:
                        item.setForeground(
                            QColor(255, 220, 130) if cat_key == "rating"
                            else QColor(200, 230, 255)
                        )
                    else:
                        item.setForeground(cat_color)
                self._global_list.addItem(item)

        self._global_list.blockSignals(False)

        n = len(self._active_filter_tags)
        self._btn_clear.setText(f"Clear filters ({n})" if n else "Clear filters")
        self._btn_clear.setEnabled(bool(n))

    def _refresh_selected_tags(self):
        """Rebuild only the Selected Image Tags section. Called on every image selection change."""
        self._selected_list.clear()
        if self._selected_image_ids:
            n_imgs = len(self._selected_image_ids)
            suffix = f" — {n_imgs} images" if n_imgs > 1 else ""
            self._selection_label.setText(f"Selected Image Tags{suffix}")
            tag_rows = db.get_tags_for_images(self._selected_image_ids)
            for row in tag_rows:
                name, count = row["name"], row["count"]
                label = name if count == n_imgs else f"{name} ({count}/{n_imgs})"
                sel_item = QListWidgetItem(label)
                sel_item.setData(Qt.ItemDataRole.UserRole, name)
                sel_item.setToolTip(name)
                if count < n_imgs:
                    sel_item.setForeground(QColor(255, 255, 255, 140))
                self._selected_list.addItem(sel_item)
        else:
            self._selection_label.setText("Selected Image Tags")

    def _on_item_changed(self, item: QListWidgetItem):
        if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return  # ignore header items
        tag_name = item.data(Qt.ItemDataRole.UserRole)
        if not tag_name:
            return
        cat = _tag_category(tag_name)
        if item.checkState() == Qt.CheckState.Checked:
            self._active_filter_tags.add(tag_name)
            new_color = QColor(255, 220, 130) if cat == "rating" else QColor(200, 230, 255)
        else:
            self._active_filter_tags.discard(tag_name)
            new_color = _CATEGORY_COLOR[cat]
        # Block signals when updating foreground to avoid re-entrant itemChanged
        self._global_list.blockSignals(True)
        item.setForeground(new_color)
        self._global_list.blockSignals(False)
        n = len(self._active_filter_tags)
        self._btn_clear.setText(f"Clear filters ({n})" if n else "Clear filters")
        self._btn_clear.setEnabled(bool(n))
        self.tag_filter_changed.emit(sorted(self._active_filter_tags), self._filter_mode)

    def _on_mode_toggled(self, checked: bool):
        self._filter_mode = "OR" if checked else "AND"
        self._mode_btn.setText("OR" if checked else "AND")
        if self._active_filter_tags:
            self.tag_filter_changed.emit(sorted(self._active_filter_tags), self._filter_mode)

    def _on_sort_toggled(self, checked: bool):
        self._sort_by_count = not checked
        self._sort_btn.setText("A-Z" if checked else "Count ↓")
        self._refresh_global_list()

    def _remove_tag(self):
        item = self._selected_list.currentItem()
        if not item or not self._selected_image_ids:
            return
        tag_name = item.data(Qt.ItemDataRole.UserRole)
        for image_id in self._selected_image_ids:
            db.remove_tag_from_image(image_id, tag_name)
        self.refresh()
