import os
from PyQt6.QtWidgets import (QLabel, QFrame, QListWidget, QListWidgetItem,
                              QLineEdit, QMessageBox, QCompleter, QToolTip,
                              QVBoxLayout, QHBoxLayout)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent, QPoint
from PyQt6.QtGui import QKeySequence, QShortcut, QCursor
from src.ui.image_viewer import ImageViewer
from src.core import database as db, file_ops

_HUD_LEGEND = "  S star   T tag   A album   D trash   ←/→ navigate   Esc close  "

_TRIAGE_LIST_QSS = (
    "QListWidget { outline: 0; background: transparent; border: none; }"
    "QListWidget::item { padding: 2px 6px; color: #ccc; }"
    "QListWidget::item:hover { background: rgba(255,255,255,0.08); border-radius: 3px; }"
    "QListWidget::item:selected { background: rgba(80,130,255,0.25); color: #fff; border-radius: 3px; }"
)


class TriageImageViewer(ImageViewer):
    """ImageViewer subclass with single-key triage actions: S star, T tag, A album, D trash."""
    image_trashed = pyqtSignal(int)   # image_id — batch-emitted on close

    def __init__(self, image_id: int, image_path: str, parent=None,
                 all_images: list[tuple[int, str]] | None = None, current_index: int = 0):
        # Initialize plain fields before super().__init__ to avoid AttributeError
        # if any Qt event fires during parent __init__ (e.g. resizeEvent, showEvent)
        self._triage_hud: QLabel | None = None
        self._tag_input_overlay: QFrame | None = None
        self._album_picker: QFrame | None = None
        self._trashed_ids: list[int] = []
        self._triage_shortcuts: list[QShortcut] = []
        super().__init__(image_id, image_path, parent, all_images, current_index)
        # QTimer requires QObject (super) to be initialized first
        self._hud_reset_timer = QTimer(self)
        self._hud_reset_timer.setSingleShot(True)
        self._hud_reset_timer.setInterval(1500)
        self._hud_reset_timer.timeout.connect(self._reset_hud_text)
        if hasattr(self, '_view'):
            self._setup_triage_hud()
            self._setup_triage_shortcuts()

    def _setup_triage_hud(self):
        # Parent to _view so geometry is relative to the image area, not the full dialog
        self._triage_hud = QLabel(_HUD_LEGEND, self._view)
        self._triage_hud.setStyleSheet(
            "QLabel { background: rgba(0,0,0,0.65); color: #888; font-size: 10px;"
            " border-top: 1px solid rgba(255,255,255,0.12); padding: 4px 10px; }"
        )
        self._triage_hud.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._triage_hud.raise_()
        self._position_hud()

    def _position_hud(self):
        if self._triage_hud:
            hud_h = self._triage_hud.sizeHint().height()
            # Geometry is in _view local coordinates (HUD is parented to _view)
            self._triage_hud.setGeometry(
                0, self._view.height() - hud_h,
                self._view.width(), hud_h
            )

    def _setup_triage_shortcuts(self):
        mappings = [
            ("S", self._triage_star),
            ("T", self._triage_tag_input),
            ("A", self._triage_album_picker),
            ("D", self._triage_delete),
        ]
        for key, slot in mappings:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)
            self._triage_shortcuts.append(sc)

    def _set_shortcuts_enabled(self, enabled: bool):
        for sc in self._triage_shortcuts:
            sc.setEnabled(enabled)

    def _flash_hud(self, msg: str):
        if self._triage_hud:
            self._triage_hud.setText(f"  {msg}  ")
            self._hud_reset_timer.start()

    def _reset_hud_text(self):
        if self._triage_hud:
            self._triage_hud.setText(_HUD_LEGEND)

    def showEvent(self, event):
        super().showEvent(event)
        self._position_hud()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._dismiss_overlays()
        self._position_hud()

    def _on_splitter_moved(self, pos: int, index: int):  # type: ignore[override]
        super()._on_splitter_moved(pos, index)
        self._position_hud()

    def _navigate(self, delta: int):
        self._dismiss_overlays()
        super()._navigate(delta)

    # ------------------------------------------------------------------ Actions

    def _triage_star(self):
        db.add_tags_to_image_batch(self.image_id, ["star"])
        self._refresh_tags_section()   # lightweight — only tags section
        self._flash_hud("★  Starred")

    def _triage_tag_input(self):
        self._dismiss_overlays()

        container = QFrame(self)
        container.setStyleSheet(
            "QFrame { background: rgba(20,20,20,0.92); border: 1px solid rgba(255,255,255,0.2);"
            " border-radius: 6px; }"
        )
        inner = QHBoxLayout(container)
        inner.setContentsMargins(8, 6, 8, 6)
        inner.setSpacing(6)

        lbl = QLabel("Tag:")
        lbl.setStyleSheet("QLabel { color: #aaa; font-size: 11px; background: transparent; border: none; }")
        inner.addWidget(lbl)

        edit = QLineEdit()
        edit.setPlaceholderText("type tag name, Enter to apply…")
        edit.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15);"
            " border-radius: 3px; color: white; font-size: 11px; padding: 2px 6px; }"
        )
        tag_names = [r["name"] for r in db.get_all_tags_with_counts()]
        completer = QCompleter(tag_names, edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        edit.setCompleter(completer)
        inner.addWidget(edit)

        container.adjustSize()
        container.setMinimumWidth(300)
        self._tag_input_overlay = container

        edit.returnPressed.connect(lambda: self._apply_triage_tag(edit.text()))
        edit.installEventFilter(self)

        self._position_overlay(container)
        container.show()
        container.raise_()
        edit.setFocus()
        self._set_shortcuts_enabled(False)

    def _apply_triage_tag(self, text: str):
        name = " ".join(text.split())
        if name:
            db.add_tags_to_image_batch(self.image_id, [name])
            self._refresh_tags_section()   # lightweight — only tags section
            self._flash_hud(f"Tagged: {name}")
        self._dismiss_overlays()

    def _triage_album_picker(self):
        self._dismiss_overlays()
        albums = db.get_all_albums_with_counts()
        if not albums:
            QToolTip.showText(
                QCursor.pos(),
                "No albums yet — create one in the Albums panel."
            )
            return

        container = QFrame(self)
        container.setStyleSheet(
            "QFrame { background: rgba(20,20,20,0.92); border: 1px solid rgba(255,255,255,0.2);"
            " border-radius: 6px; }"
        )
        inner = QVBoxLayout(container)
        inner.setContentsMargins(6, 6, 6, 6)
        inner.setSpacing(4)

        hint = QLabel("Add to album — double-click or Enter:")
        hint.setStyleSheet("QLabel { color: #888; font-size: 10px; background: transparent; border: none; }")
        inner.addWidget(hint)

        lst = QListWidget()
        lst.setStyleSheet(_TRIAGE_LIST_QSS)
        for alb in albums:
            item = QListWidgetItem(f"{alb['name']} ({alb['count']})")
            item.setData(Qt.ItemDataRole.UserRole, alb["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, alb["name"])
            lst.addItem(item)
        lst.setMaximumHeight(min(len(albums) * 26 + 8, 180))
        lst.setCurrentRow(0)
        lst.itemDoubleClicked.connect(
            lambda it: self._apply_triage_album(
                it.data(Qt.ItemDataRole.UserRole),
                it.data(Qt.ItemDataRole.UserRole + 1)
            )
        )
        lst.installEventFilter(self)
        inner.addWidget(lst)

        container.adjustSize()
        container.setMinimumWidth(240)
        self._album_picker = container

        self._position_overlay(container)
        container.show()
        container.raise_()
        lst.setFocus()
        self._set_shortcuts_enabled(False)

    def _apply_triage_album(self, album_id: int, album_name: str):
        db.add_image_to_album(album_id, self.image_id)
        self._dismiss_overlays()
        self._refresh_albums_section()   # update albums list in detail panel
        self._flash_hud(f"Added to: {album_name}")

    def _triage_delete(self):
        old_id = self.image_id
        old_index = self._current_index
        try:
            file_ops.delete_image(old_id, use_trash=True)
        except Exception as e:
            QMessageBox.warning(self, "Delete Error", str(e))
            return
        self._trashed_ids.append(old_id)
        self._all_images.pop(old_index)
        if not self._all_images:
            for iid in self._trashed_ids:
                self.image_trashed.emit(iid)
            self._trashed_ids.clear()
            self.accept()
            return
        self._current_index = min(old_index, len(self._all_images) - 1)
        self.image_id, self.image_path = self._all_images[self._current_index]
        self.setWindowTitle(os.path.basename(self.image_path))
        self._path_label.setText(os.path.basename(self.image_path))
        self._path_label.setToolTip(self.image_path)
        self._load_image()
        self._update_nav_buttons()
        self._flash_hud("Trashed")

    # ------------------------------------------------------------------ Overlays

    def _position_overlay(self, container: QFrame):
        """Centre overlay horizontally over the image view, just above the HUD."""
        container.adjustSize()
        hud_h = self._triage_hud.sizeHint().height() if self._triage_hud else 30
        # Compute position in _view local coords, then map to dialog coords
        x = max(0, (self._view.width() - container.sizeHint().width()) // 2)
        y = max(0, self._view.height() - hud_h - container.sizeHint().height() - 8)
        container.move(self._view.mapTo(self, QPoint(x, y)))

    def _dismiss_overlays(self):
        for attr in ("_tag_input_overlay", "_album_picker"):
            w = getattr(self, attr, None)
            if w:
                w.deleteLater()
                setattr(self, attr, None)
        self._set_shortcuts_enabled(True)
        if hasattr(self, '_view'):
            self._view.setFocus()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._dismiss_overlays()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._album_picker:
                    lst = self._album_picker.findChild(QListWidget)
                    if lst and lst.currentItem():
                        it = lst.currentItem()
                        self._apply_triage_album(
                            it.data(Qt.ItemDataRole.UserRole),
                            it.data(Qt.ItemDataRole.UserRole + 1)
                        )
                    return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        for iid in self._trashed_ids:
            self.image_trashed.emit(iid)
        super().closeEvent(event)
