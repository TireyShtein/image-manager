import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QGraphicsView, QGraphicsScene,
                              QFrame, QListWidget, QListWidgetItem, QLineEdit,
                              QMessageBox, QCompleter, QMenu, QToolTip,
                              QSplitter, QScrollArea, QGridLayout, QWidget)
from PyQt6.QtCore import (Qt, QRectF, QRunnable, QThreadPool, QObject,
                          pyqtSignal, pyqtSlot, QTimer, QEvent, QStringListModel,
                          QPoint, QSettings)
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


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("color: rgba(255,255,255,0.10);")
    return sep


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
            self._open_with_system(image_path)
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self.close)
            self.resize(1, 1)
            return
        self.resize(1200, 750)
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
        layout.setSpacing(0)

        # --- Horizontal splitter: image view (left) + detail panel (right) ---
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(4)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: rgba(255,255,255,0.08); }"
        )

        self._view = ZoomableGraphicsView(self)
        self._view.setMinimumWidth(400)
        self._splitter.addWidget(self._view)

        self._build_detail_panel()
        self._splitter.addWidget(self._detail_scroll)

        self._splitter.setStretchFactor(0, 1)   # view stretches
        self._splitter.setStretchFactor(1, 0)   # panel does not stretch
        self._splitter.setSizes([900, 300])
        self._splitter.splitterMoved.connect(self._on_splitter_moved)

        # Restore persisted splitter position
        state = QSettings("ImageManager", "ImageManager").value("viewer_splitter_state")
        if state:
            self._splitter.restoreState(state)

        layout.addWidget(self._splitter, 1)

        # --- Bottom nav bar (outside splitter, fixed height) ---
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

        layout.addLayout(bar, 0)
        self._view._zoom_callback = self._on_zoom_changed
        self._update_nav_buttons()

    def _build_detail_panel(self):
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setMinimumWidth(200)
        self._detail_scroll.setMaximumWidth(450)
        self._detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._detail_scroll.setStyleSheet(
            "QScrollArea { border-left: 1px solid rgba(255,255,255,0.10); }"
        )

        self._detail_widget = QWidget()  # no explicit parent — setWidget takes ownership
        panel = QVBoxLayout(self._detail_widget)
        panel.setContentsMargins(12, 12, 12, 12)
        panel.setSpacing(6)

        # --- Filename + path ---
        self._detail_filename = QLabel()
        fn_font = self._detail_filename.font()
        fn_font.setWeight(QFont.Weight.Bold)
        self._detail_filename.setFont(fn_font)
        self._detail_filename.setWordWrap(True)
        panel.addWidget(self._detail_filename)

        self._detail_path = QLabel()
        self._detail_path.setWordWrap(True)
        self._detail_path.setStyleSheet("color: rgba(180,180,180,0.65); font-size: 10px;")
        panel.addWidget(self._detail_path)

        panel.addWidget(_make_separator())

        # --- Metadata grid ---
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 70)

        _hdr_style = "color: rgba(180,180,180,0.55); font-size: 10px;"
        _val_style = "font-size: 11px;"

        for row_idx, (label_text, attr) in enumerate([
            ("Dimensions", "_detail_dims"),
            ("File size",  "_detail_filesize"),
            ("Added",      "_detail_date_added"),
            ("Modified",   "_detail_date_modified"),
        ]):
            hdr = QLabel(label_text)
            hdr.setStyleSheet(_hdr_style)
            val = QLabel("—")
            val.setStyleSheet(_val_style)
            val.setWordWrap(True)
            grid.addWidget(hdr, row_idx, 0)
            grid.addWidget(val, row_idx, 1)
            setattr(self, attr, val)

        panel.addWidget(grid_widget)
        panel.addWidget(_make_separator())

        # --- Albums section ---
        alb_hdr = QLabel("Albums")
        alb_font = alb_hdr.font()
        alb_font.setWeight(QFont.Weight.DemiBold)
        alb_hdr.setFont(alb_font)
        alb_hdr.setStyleSheet("font-size: 11px;")
        panel.addWidget(alb_hdr)

        self._detail_albums = QLabel("—")
        self._detail_albums.setWordWrap(True)
        self._detail_albums.setStyleSheet("font-size: 11px; color: #b4c7d9;")
        panel.addWidget(self._detail_albums)

        panel.addWidget(_make_separator())

        # --- Tags section ---
        tags_hdr = QLabel("Tags")
        tags_font = tags_hdr.font()
        tags_font.setWeight(QFont.Weight.DemiBold)
        tags_hdr.setFont(tags_font)
        tags_hdr.setStyleSheet("font-size: 11px;")
        panel.addWidget(tags_hdr)

        self._detail_rating_tags = QLabel()
        self._detail_rating_tags.setWordWrap(True)
        self._detail_rating_tags.setStyleSheet("font-size: 11px; color: #f5a623;")
        self._detail_rating_tags.hide()
        panel.addWidget(self._detail_rating_tags)

        self._detail_general_tags = QLabel()
        self._detail_general_tags.setWordWrap(True)
        self._detail_general_tags.setStyleSheet("font-size: 11px; color: #b4c7d9;")
        panel.addWidget(self._detail_general_tags)

        panel.addStretch(1)

        self._detail_scroll.setWidget(self._detail_widget)

    # ------------------------------------------------------------------ Detail panel refresh

    def _refresh_detail_panel(self):
        self._refresh_metadata_section()
        self._refresh_albums_section()
        self._refresh_tags_section()

    def _refresh_metadata_section(self):
        from src.core import database as db
        basename = os.path.basename(self.image_path)
        self._detail_filename.setText(basename)
        self._detail_path.setText(self.image_path)
        self._detail_path.setToolTip(self.image_path)

        row = db.get_image(self.image_id)
        if row:
            if row["width"] and row["height"]:
                self._detail_dims.setText(f"{row['width']}×{row['height']}")
            else:
                self._detail_dims.setText("—")

            if row["file_size"]:
                size_b = row["file_size"]
                size_str = (f"{size_b / (1024*1024):.1f} MB"
                            if size_b >= 1024*1024 else f"{size_b // 1024} KB")
                self._detail_filesize.setText(size_str)
            else:
                self._detail_filesize.setText("—")

            def _fmt(iso: str | None) -> str:
                if not iso:
                    return "—"
                return iso[:16].replace("T", "  ")

            self._detail_date_added.setText(_fmt(row["date_added"]))
            self._detail_date_modified.setText(_fmt(row["date_modified"]))
        else:
            for attr in ("_detail_dims", "_detail_filesize",
                         "_detail_date_added", "_detail_date_modified"):
                getattr(self, attr).setText("—")

    def _refresh_albums_section(self):
        from src.core import database as db
        albums = db.get_albums_for_image(self.image_id)
        if albums:
            self._detail_albums.setText(", ".join(name for _, name in albums))
        else:
            self._detail_albums.setText("—")

    def _refresh_tags_section(self):
        from src.core import database as db
        rows = db.get_tags_for_images([self.image_id])
        rating_tags = [r["name"] for r in rows if r["name"].startswith("rating:")]
        general_tags = sorted(r["name"] for r in rows if not r["name"].startswith("rating:"))

        if rating_tags:
            self._detail_rating_tags.setText("  ·  ".join(rating_tags))
            self._detail_rating_tags.show()
        else:
            self._detail_rating_tags.hide()

        self._detail_general_tags.setText(
            "  ·  ".join(general_tags) if general_tags else "No tags"
        )

    # ------------------------------------------------------------------ Image loading

    def _load_image(self):
        self._refresh_detail_panel()
        self._set_nav_enabled(False)
        loading_scene = QGraphicsScene()
        loading_scene.addText("Loading…", QFont("Arial", 2))
        self._view.setScene(loading_scene)
        worker = _ImageLoadRunnable(self.image_path, self._load_signals)
        QThreadPool.globalInstance().start(worker)

    def _on_image_loaded(self, img: QImage, path: str):
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
        # Fill in dims/filesize only if DB had no values (shown as "—")
        if self._detail_dims.text() == "—":
            self._detail_dims.setText(f"{pixmap.width()}×{pixmap.height()}")
        if self._detail_filesize.text() == "—":
            try:
                size_b = os.path.getsize(self.image_path)
                size_str = (f"{size_b / (1024*1024):.1f} MB"
                            if size_b >= 1024*1024 else f"{size_b // 1024} KB")
                self._detail_filesize.setText(size_str)
            except OSError:
                pass
        self._fit_mode = True
        self._fit()
        self._set_nav_enabled(True)

    # ------------------------------------------------------------------ Navigation

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

    def closeEvent(self, event):
        if hasattr(self, '_splitter'):
            QSettings("ImageManager", "ImageManager").setValue(
                "viewer_splitter_state", self._splitter.saveState()
            )
        super().closeEvent(event)

    def _on_zoom_changed(self, zoom: float):
        self._zoom_label.setText(f"{int(zoom * 100)}%")
        self._fit_mode = False

    def _on_splitter_moved(self, pos: int, index: int):
        if self._fit_mode:
            self._fit()

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
        from src.core import database as db
        db.add_tags_to_image_batch(self.image_id, ["star"])
        self._refresh_tags_section()   # lightweight — only tags section
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
            self._refresh_tags_section()   # lightweight — only tags section
            self._flash_hud(f"Tagged: {name}")
        self._dismiss_overlays()

    def _triage_album_picker(self):
        self._dismiss_overlays()
        from src.core import database as db
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
        from src.core import database as db
        db.add_image_to_album(album_id, self.image_id)
        self._dismiss_overlays()
        self._refresh_albums_section()   # update albums list in detail panel
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
