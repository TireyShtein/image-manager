import json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QPushButton, QLineEdit, QLabel,
                              QInputDialog, QMessageBox, QFrame, QMenu)
from PyQt6.QtCore import pyqtSignal, Qt, QEvent
from PyQt6.QtGui import QFont, QColor
from src.core import database as db

_LIST_QSS = (
    "QListWidget { outline: 0; }"
    "QListWidget::item { padding: 2px 4px; }"
    "QListWidget::item:hover { background: rgba(100, 150, 255, 0.10); border-radius: 4px; }"
    "QListWidget::item:selected { background: rgba(80, 130, 255, 0.22); "
    "color: palette(text); border-radius: 4px; }"
)
_MIME_IMAGE_IDS = "application/x-imagemanager-ids"

_BTN_QSS = (
    "QPushButton { padding: 3px 8px; border: 1px solid rgba(255,255,255,0.15); "
    "border-radius: 4px; }"
    "QPushButton:hover { background: rgba(100, 150, 255, 0.15); }"
    "QPushButton:disabled { color: rgba(255,255,255,0.30); "
    "border-color: rgba(255,255,255,0.10); }"
)


class AlbumPanel(QWidget):
    album_selected = pyqtSignal(int)           # album_id
    collection_selected = pyqtSignal(int)      # filter_id
    images_added_to_album = pyqtSignal(int, str)  # (count, album_name)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_image_ids: list[int] = []
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 4)
        layout.setSpacing(4)

        lbl = QLabel("Albums")
        font = lbl.font()
        font.setWeight(QFont.Weight.DemiBold)
        lbl.setFont(font)
        layout.addWidget(lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)
        layout.addSpacing(2)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_QSS)
        self._list.itemDoubleClicked.connect(self._on_album_double_clicked)
        self._list.currentItemChanged.connect(lambda *_: self._update_button_states())
        self._list.setAcceptDrops(True)
        self._list.viewport().setAcceptDrops(True)
        self._list.viewport().installEventFilter(self)
        self._highlighted_album_item = None
        layout.addWidget(self._list)

        row = QHBoxLayout()
        self._album_input = QLineEdit()
        self._album_input.setPlaceholderText("New album…")
        self._album_input.returnPressed.connect(self._create_album)
        row.addWidget(self._album_input)

        btn_add = QPushButton("+")
        btn_add.setFixedWidth(28)
        btn_add.setStyleSheet(_BTN_QSS)
        btn_add.clicked.connect(self._create_album)
        row.addWidget(btn_add)

        layout.addLayout(row)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addSpacing(2)
        layout.addWidget(sep2)
        layout.addSpacing(2)

        self._btn_add_imgs = QPushButton("Add selected to album")
        self._btn_add_imgs.setStyleSheet(_BTN_QSS)
        self._btn_add_imgs.clicked.connect(self._add_images_to_album)
        layout.addWidget(self._btn_add_imgs)

        self._btn_remove_imgs = QPushButton("Remove selected from album")
        self._btn_remove_imgs.setStyleSheet(_BTN_QSS)
        self._btn_remove_imgs.clicked.connect(self._remove_images_from_album)
        layout.addWidget(self._btn_remove_imgs)

        self._btn_rename = QPushButton("Rename album")
        self._btn_rename.setStyleSheet(_BTN_QSS)
        self._btn_rename.clicked.connect(self._rename_album)
        layout.addWidget(self._btn_rename)

        self._btn_delete = QPushButton("Delete album")
        self._btn_delete.setStyleSheet(_BTN_QSS)
        self._btn_delete.clicked.connect(self._delete_album)
        layout.addWidget(self._btn_delete)

        self._update_button_states()

        # Smart Collections section
        layout.addSpacing(8)

        lbl_collections = QLabel("Smart Collections")
        font2 = lbl_collections.font()
        font2.setWeight(QFont.Weight.DemiBold)
        lbl_collections.setFont(font2)
        layout.addWidget(lbl_collections)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep3)
        layout.addSpacing(2)

        self._collections_list = QListWidget()
        self._collections_list.setStyleSheet(_LIST_QSS)
        self._collections_list.setMaximumHeight(150)
        self._collections_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._collections_list.itemDoubleClicked.connect(self._on_collection_double_clicked)
        self._collections_list.customContextMenuRequested.connect(self._on_collection_context_menu)
        layout.addWidget(self._collections_list)

    def _update_button_states(self):
        has_album = self._list.currentItem() is not None
        has_images = bool(self._selected_image_ids)
        self._btn_add_imgs.setEnabled(has_album and has_images)
        self._btn_remove_imgs.setEnabled(has_album and has_images)
        self._btn_rename.setEnabled(has_album)
        self._btn_delete.setEnabled(has_album)

    def set_selected_images(self, image_ids: list[int]):
        self._selected_image_ids = image_ids
        self._update_button_states()

    # ------------------------------------------------------------------
    # Drag-and-drop: album list drop target
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is not self._list.viewport():
            return super().eventFilter(obj, event)
        t = event.type()
        if t == QEvent.Type.DragEnter:
            # Accept for the whole widget if MIME type matches — DragMove handles
            # per-position accept/ignore. Ignoring DragEnter blocks all DragMove
            # delivery, so we must accept here unconditionally for our MIME type.
            if event.mimeData().hasFormat(_MIME_IMAGE_IDS):
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
            else:
                event.ignore()
            return True
        if t == QEvent.Type.DragMove:
            item = self._list.itemAt(event.position().toPoint())
            self._set_album_highlight(item)
            if item and event.mimeData().hasFormat(_MIME_IMAGE_IDS):
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
            else:
                event.ignore()
            return True
        if t == QEvent.Type.DragLeave:
            self._set_album_highlight(None)
            return True
        if t == QEvent.Type.Drop:
            self._set_album_highlight(None)
            item = self._list.itemAt(event.position().toPoint())
            if not item or not event.mimeData().hasFormat(_MIME_IMAGE_IDS):
                event.ignore()
                return True
            album_id = item.data(Qt.ItemDataRole.UserRole)
            album_name = item.text().rsplit(" (", 1)[0]
            try:
                ids = json.loads(bytes(event.mimeData().data(_MIME_IMAGE_IDS)).decode())
            except (ValueError, KeyError):
                event.ignore()
                return True
            for image_id in ids:
                db.add_image_to_album(album_id, image_id)
            self.refresh()
            event.acceptProposedAction()
            self.images_added_to_album.emit(len(ids), album_name)
            return True
        return super().eventFilter(obj, event)

    def _set_album_highlight(self, item):
        if self._highlighted_album_item and self._highlighted_album_item is not item:
            self._highlighted_album_item.setBackground(Qt.GlobalColor.transparent)
        self._highlighted_album_item = item
        if item:
            item.setBackground(QColor(80, 130, 255, 110))

    def refresh(self):
        self._refresh_albums()
        self._refresh_collections()

    def _refresh_albums(self):
        self._list.clear()
        for row in db.get_all_albums():
            count = db.get_album_image_count(row["id"])
            item = QListWidgetItem(f"{row['name']} ({count})")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self._list.addItem(item)

    def _refresh_collections(self):
        self._collections_list.clear()
        for row in db.get_all_saved_filters():
            try:
                tags = json.loads(row["tags"])
            except (ValueError, TypeError):
                tags = []
            item = QListWidgetItem(row["name"])
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(f"[{row['mode']}] {', '.join(tags) or '(no tags)'}")
            self._collections_list.addItem(item)

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
        self._update_button_states()

    def _remove_images_from_album(self):
        item = self._list.currentItem()
        if not item or not self._selected_image_ids:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        for image_id in self._selected_image_ids:
            db.remove_image_from_album(album_id, image_id)
        self.refresh()
        self._update_button_states()

    def _rename_album(self):
        item = self._list.currentItem()
        if not item:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        current_name = item.text().rsplit(" (", 1)[0]
        new_name, ok = QInputDialog.getText(self, "Rename Album", "New name:", text=current_name)
        if ok and new_name.strip() and new_name.strip() != current_name:
            db.rename_album(album_id, new_name.strip())
            self.refresh()

    def _delete_album(self):
        item = self._list.currentItem()
        if not item:
            return
        album_id = item.data(Qt.ItemDataRole.UserRole)
        album_name = item.text().rsplit(" (", 1)[0]
        reply = QMessageBox.question(self, "Delete Album",
                                     f"Delete album '{album_name}'? Images will not be deleted.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_album(album_id)
            self.refresh()

    def _on_album_double_clicked(self, item: QListWidgetItem):
        album_id = item.data(Qt.ItemDataRole.UserRole)
        self.album_selected.emit(album_id)

    def _on_collection_double_clicked(self, item: QListWidgetItem):
        filter_id = item.data(Qt.ItemDataRole.UserRole)
        self.collection_selected.emit(filter_id)

    def _on_collection_context_menu(self, pos):
        item = self._collections_list.itemAt(pos)
        if not item:
            return
        filter_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_collection(filter_id))
        menu.addAction("Delete", lambda: self._delete_collection(filter_id))
        menu.exec(self._collections_list.mapToGlobal(pos))

    def _rename_collection(self, filter_id: int):
        row = db.get_saved_filter(filter_id)
        if not row:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Collection", "New name:", text=row["name"]
        )
        if ok and new_name.strip() and new_name.strip() != row["name"]:
            try:
                db.rename_saved_filter(filter_id, new_name.strip())
                self._refresh_collections()
            except Exception:
                QMessageBox.warning(
                    self, "Rename Failed",
                    "A collection with that name already exists."
                )

    def _delete_collection(self, filter_id: int):
        row = db.get_saved_filter(filter_id)
        if not row:
            return
        reply = QMessageBox.question(
            self, "Delete Collection",
            f"Delete smart collection '{row['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_saved_filter(filter_id)
            self._refresh_collections()
