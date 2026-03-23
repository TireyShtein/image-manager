import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QGraphicsView, QGraphicsScene,
                              QFrame, QListWidget, QListWidgetItem, QLineEdit,
                              QMessageBox, QCompleter, QMenu, QToolTip)
from PyQt6.QtCore import (Qt, QRectF, QRunnable, QThreadPool, QObject,
                          pyqtSignal, pyqtSlot, QTimer, QEvent, QStringListModel)
from PyQt6.QtGui import (QPixmap, QImage, QFont, QWheelEvent, QKeyEvent,
                         QKeySequence, QShortcut, QCursor)
import subprocess
import sys


class _ImageLoadSignals(QObject):
    loaded = pyqtSignal(object, str)  # (QImage, path)


class _ImageLoadRunnable(QRunnable):
    def __init__(self, path: str, signals: _ImageLoadSignals):
        super().__init__()
        self._path = path
        self._signals = signals
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        img = QImage(self._path)
        self._signals.loaded.emit(img, self._path)

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.wmv', '.flv', '.m4v'}


class ImageViewer(QDialog):
    def __init__(self, image_id: int, image_path: str, parent=None,
                 all_images: list[tuple[int, str]] | None = None, current_index: int = 0):
        super().__init__(parent)
        self.image_id = image_id
        self.image_path = image_path
        self._all_images = all_images or []
        self._current_index = current_index
        self.setWindowTitle(os.path.basename(image_path))
        ext = os.path.splitext(image_path)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            # Open video with the system default player and close this dialog
            self._open_with_system(image_path)
            # Schedule close after exec() starts
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self.close)
            self.resize(1, 1)
            return
        self.resize(900, 700)
        self._fit_mode = True
        self._load_signals = _ImageLoadSignals()
        self._load_signals.loaded.connect(self._on_image_loaded)
        self._setup_ui()
        self._load_image()

    def _open_with_system(self, path: str):
        if sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = ZoomableGraphicsView(self)
        layout.addWidget(self._view)

        # Tag strip — shows tags for the current image
        self._tags_label = QLabel("No tags")
        self._tags_label.setWordWrap(True)
        self._tags_label.setMaximumHeight(46)
        self._tags_label.setStyleSheet(
            "QLabel { color: #999; font-size: 10px; background: rgba(0,0,0,0.25);"
            " padding: 4px 8px; border-top: 1px solid rgba(255,255,255,0.07); }"
        )
        layout.addWidget(self._tags_label)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)

        has_nav = len(self._all_images) > 1
        self._btn_prev = QPushButton("←")
        self._btn_prev.setFixedWidth(36)
        self._btn_prev.setEnabled(has_nav)
        self._btn_prev.clicked.connect(lambda: self._navigate(-1))
        bar.addWidget(self._btn_prev)

        self._path_label = QLabel(os.path.basename(self.image_path))
        self._path_label.setToolTip(self.image_path)
        self._path_label.setStyleSheet("color: gray; font-size: 11px;")
        bar.addWidget(self._path_label, 1)

        self._btn_next = QPushButton("→")
        self._btn_next.setFixedWidth(36)
        self._btn_next.setEnabled(has_nav)
        self._btn_next.clicked.connect(lambda: self._navigate(1))
        bar.addWidget(self._btn_next)

        self._meta_label = QLabel("")
        self._meta_label.setStyleSheet("color: gray; font-size: 11px;")
        bar.addWidget(self._meta_label)

        self._zoom_label = QLabel("Fit")
        self._zoom_label.setFixedWidth(52)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setStyleSheet("color: gray; font-size: 11px;")
        bar.addWidget(self._zoom_label)

        btn_fit = QPushButton("Fit")
        btn_fit.setFixedWidth(60)
        btn_fit.clicked.connect(self._fit)
        bar.addWidget(btn_fit)

        btn_100 = QPushButton("100%")
        btn_100.setFixedWidth(60)
        btn_100.clicked.connect(self._actual_size)
        bar.addWidget(btn_100)

        layout.addLayout(bar)
        self._view._zoom_callback = self._on_zoom_changed
        self._update_nav_buttons()

    def _refresh_tags(self):
        from src.core import database as db
        rows = db.get_tags_for_images([self.image_id])
        if not rows:
            self._tags_label.setText("No tags")
            return
        rating = [r["name"] for r in rows if r["name"].startswith("rating:")]
        others = sorted(r["name"] for r in rows if not r["name"].startswith("rating:"))
        all_tags = rating + others
        self._tags_label.setText("  ·  ".join(all_tags))

    def _load_image(self):
        self._refresh_tags()
        # Show a loading placeholder while decode runs on a background thread
        self._set_nav_enabled(False)
        loading_scene = QGraphicsScene()
        loading_scene.addText("Loading…", QFont("Arial", 2))
        self._view.setScene(loading_scene)
        self._meta_label.setText("")
        worker = _ImageLoadRunnable(self.image_path, self._load_signals)
        QThreadPool.globalInstance().start(worker)

    def _on_image_loaded(self, img: QImage, path: str):
        # Ignore if user navigated away before this finished
        if path != self.image_path:
            return
        if img.isNull():
            scene = QGraphicsScene()
            scene.addText("Failed to load image")
            self._view.setScene(scene)
            self._set_nav_enabled(True)
            return
        pixmap = QPixmap.fromImage(img)
        scene = QGraphicsScene()
        scene.addPixmap(pixmap)
        self._view.setScene(scene)
        self._view.setSceneRect(QRectF(pixmap.rect()))
        w, h = pixmap.width(), pixmap.height()
        try:
            size_b = os.path.getsize(self.image_path)
            size_str = f"{size_b / (1024*1024):.1f} MB" if size_b >= 1024*1024 else f"{size_b // 1024} KB"
        except OSError:
            size_str = "?"
        self._meta_label.setText(f"{w}×{h}  {size_str}")
        self._fit_mode = True
        self._fit()
        self._set_nav_enabled(True)

    def _update_nav_buttons(self):
        has_nav = len(self._all_images) > 1
        self._btn_prev.setEnabled(has_nav and self._current_index > 0)
        self._btn_next.setEnabled(has_nav and self._current_index < len(self._all_images) - 1)

    def _set_nav_enabled(self, enabled: bool):
        if enabled:
            self._update_nav_buttons()
        else:
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_view'):
            self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_view') and self._view.scene() and self._fit_mode:
            self._fit()

    def _on_zoom_changed(self, zoom: float):
        self._zoom_label.setText(f"{int(zoom * 100)}%")
        self._fit_mode = False

    def _navigate(self, delta: int):
        if not self._all_images:
            return
        new_index = self._current_index + delta
        if new_index < 0 or new_index >= len(self._all_images):
            return
        self._current_index = new_index
        self.image_id, self.image_path = self._all_images[self._current_index]
        self.setWindowTitle(os.path.basename(self.image_path))
        self._path_label.setText(os.path.basename(self.image_path))
        self._path_label.setToolTip(self.image_path)
        self._load_image()
        self._update_nav_buttons()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Left:
            self._navigate(-1)
        elif event.key() == Qt.Key.Key_Right:
            self._navigate(1)
        else:
            super().keyPressEvent(event)

    def _fit(self):
        self._view.fitInView(self._view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        zoom = self._view.transform().m11()
        self._view._zoom = zoom
        self._zoom_label.setText(f"{int(zoom * 100)}%")
        self._fit_mode = True

    def _actual_size(self):
        self._view.resetTransform()
        self._view._zoom = 1.0
        self._zoom_label.setText("100%")
        self._fit_mode = False


class ZoomableGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._zoom = 1.0
        self._zoom_callback = None  # set by ImageViewer after _setup_ui

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom *= factor
        self._zoom = max(0.05, min(self._zoom, 50.0))
        self.scale(factor, factor)
        if self._zoom_callback:
            self._zoom_callback(self._zoom)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.parent().close()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.parent().keyPressEvent(event)
        else:
            super().keyPressEvent(event)


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
        super().__init__(image_id, image_path, parent, all_images, current_index)
        self._triage_hud: QLabel | None = None
        self._tag_input_overlay: QFrame | None = None
        self._album_picker: QFrame | None = None
        self._trashed_ids: list[int] = []
        self._triage_shortcuts: list[QShortcut] = []
        self._hud_reset_timer = QTimer(self)
        self._hud_reset_timer.setSingleShot(True)
        self._hud_reset_timer.setInterval(1500)
        self._hud_reset_timer.timeout.connect(self._reset_hud_text)
        # Only set up triage controls if _setup_ui was called (non-video path)
        if hasattr(self, '_view'):
            self._setup_triage_hud()
            self._setup_triage_shortcuts()

    def _setup_triage_hud(self):
        self._triage_hud = QLabel(_HUD_LEGEND, self)
        self._triage_hud.setStyleSheet(
            "QLabel { background: rgba(0,0,0,0.65); color: #888; font-size: 10px;"
            " border-top: 1px solid rgba(255,255,255,0.12); padding: 4px 10px; }"
        )
        self._triage_hud.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._triage_hud.raise_()

    def _position_hud(self):
        if self._triage_hud:
            hud_h = self._triage_hud.sizeHint().height()
            self._triage_hud.setGeometry(0, self.height() - hud_h, self.width(), hud_h)

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

    def _navigate(self, delta: int):
        self._dismiss_overlays()
        super()._navigate(delta)

    # ------------------------------------------------------------------ Actions

    def _triage_star(self):
        from src.core import database as db
        db.add_tags_to_image_batch(self.image_id, ["star"])
        self._refresh_tags()
        self._flash_hud("★  Starred")

    def _triage_tag_input(self):
        self._dismiss_overlays()
        from src.core import database as db

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
        # Autocomplete from DB tags
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
            from src.core import database as db
            db.add_tags_to_image_batch(self.image_id, [name])
            self._refresh_tags()
            self._flash_hud(f"Tagged: {name}")
        self._dismiss_overlays()

    def _triage_album_picker(self):
        self._dismiss_overlays()
        from src.core import database as db
        albums = db.get_all_albums()
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
            count = db.get_album_image_count(alb["id"])
            item = QListWidgetItem(f"{alb['name']} ({count})")
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
        from src.core import database as db
        db.add_image_to_album(album_id, self.image_id)
        self._dismiss_overlays()
        self._flash_hud(f"Added to: {album_name}")

    def _triage_delete(self):
        from src.core import file_ops
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
            # accept() does not trigger closeEvent in PyQt6 — emit before closing
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
        """Centre overlay horizontally, just above the HUD."""
        container.adjustSize()
        hud_h = self._triage_hud.sizeHint().height() if self._triage_hud else 30
        x = max(0, (self.width() - container.sizeHint().width()) // 2)
        y = self.height() - hud_h - container.sizeHint().height() - 8
        container.move(x, max(0, y))

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
