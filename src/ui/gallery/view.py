import json
import os
from PyQt6.QtWidgets import (QListView, QAbstractItemView, QStyle, QFrame, QLabel,
                             QVBoxLayout, QApplication, QWidget, QPushButton)
from PyQt6.QtCore import (Qt, QModelIndex, QSize,
                           QThreadPool, pyqtSignal, pyqtSlot,
                           QTimer, QPoint, QEvent, QByteArray, QMimeData)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QDrag
from src.core import database as db
from src.ui.gallery.constants import _compute_thumb_size, _DENSITY_CONFIG, _MIME_IMAGE_IDS
from src.ui.gallery.pager import GalleryPager, LoadResult
from src.ui.gallery.model import GalleryModel
from src.ui.gallery.workers import (FolderLoaderSignals, FolderLoaderRunnable,
                                     _HoverCardSignals, _HoverCardRunnable)

_CARD_STYLE_IDLE = (
    "#emptyCard { background: #1c1c1c; border: 1px dashed rgba(255,255,255,0.14);"
    " border-radius: 10px; }"
)
_CARD_STYLE_DRAG = (
    "#emptyCard { background: #252525; border: 2px solid rgba(220,220,220,0.90);"
    " border-radius: 10px; }"
)


class _EmptyStateOverlay(QWidget):
    """Actionable empty-state card shown when the gallery has no images."""

    def __init__(self, parent):  # parent is GalleryView
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAcceptDrops(True)
        self._last_recent_paths: list = []

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("emptyCard")
        card.setMaximumWidth(360)
        card.setStyleSheet(_CARD_STYLE_IDLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 28, 32, 28)
        card_layout.setSpacing(8)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel()
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        icon_label.setPixmap(icon.pixmap(40, 40))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        heading = QLabel("Open a folder to get started")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("color: #e8e8e8; font-size: 15px; font-weight: 600;")

        open_btn = QPushButton("  Open Folder\u2026")
        open_btn.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_btn.setStyleSheet(
            "QPushButton { background: #2e2e2e; color: #e8e8e8;"
            " border: 1px solid rgba(255,255,255,0.22); border-radius: 6px;"
            " padding: 7px 20px; font-size: 13px; }"
            "QPushButton:hover { background: #383838; }"
            "QPushButton:pressed { background: #212121; }"
        )
        open_btn.clicked.connect(lambda: parent.open_folder_requested.emit())

        drag_hint = QLabel("or drag a folder here")
        drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drag_hint.setStyleSheet("color: #7a7a7a; font-size: 11px;")

        card_layout.addWidget(icon_label)
        card_layout.addSpacing(4)
        card_layout.addWidget(heading)
        card_layout.addSpacing(6)
        card_layout.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(drag_hint)

        # Recent folders section — hidden until there are valid entries
        self._recent_section = QWidget()
        recent_layout = QVBoxLayout(self._recent_section)
        recent_layout.setContentsMargins(0, 4, 0, 0)
        recent_layout.setSpacing(2)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: rgba(255,255,255,0.09);")
        recent_layout.addWidget(divider)
        recent_header = QLabel("Recent Folders")
        recent_header.setStyleSheet("color: #767676; font-size: 11px; font-weight: 500;"
                                    " margin-top: 4px;")
        recent_layout.addWidget(recent_header)
        self._recent_buttons_layout = QVBoxLayout()
        self._recent_buttons_layout.setSpacing(1)
        recent_layout.addLayout(self._recent_buttons_layout)
        self._recent_section.hide()
        card_layout.addWidget(self._recent_section)

        outer.addWidget(card)
        self._card = card

    def set_recent_folders(self, paths: list):
        if paths == self._last_recent_paths:
            return
        self._last_recent_paths = list(paths)
        while self._recent_buttons_layout.count():
            item = self._recent_buttons_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        valid = [p for p in paths if os.path.isdir(p)]
        for path in valid[:5]:
            parent_name = os.path.basename(os.path.dirname(path))
            label = f"{parent_name}/{os.path.basename(path)}" if parent_name else os.path.basename(path)
            btn = QPushButton(label)
            btn.setToolTip(path)
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #9a9a9a; border: none;"
                " font-size: 11px; text-align: left; padding: 2px 4px; }"
                "QPushButton:hover { color: #d4d4d4; text-decoration: underline; }"
            )
            btn.clicked.connect(lambda _checked, p=path: self.parent().recent_folder_requested.emit(p))
            self._recent_buttons_layout.addWidget(btn)
        self._recent_section.setVisible(bool(valid))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if os.path.isdir(url.toLocalFile()):
                    self._card.setStyleSheet(_CARD_STYLE_DRAG)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and any(
            os.path.isdir(url.toLocalFile()) for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._card.setStyleSheet(_CARD_STYLE_IDLE)

    def dropEvent(self, event):
        self._card.setStyleSheet(_CARD_STYLE_IDLE)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.parent().folder_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class GalleryView(QListView):
    image_double_clicked = pyqtSignal(int)
    selection_changed = pyqtSignal(list)
    context_menu_requested = pyqtSignal(list, object)
    empty_context_menu_requested = pyqtSignal(object)  # global pos
    thumbnails_loading = pyqtSignal(int, int)  # (loaded, total)
    thumbnails_ready = pyqtSignal(int)          # total count
    page_changed = pyqtSignal(int, int, int)    # (page, page_count, total)
    tags_recovered = pyqtSignal(int)            # recovered_count (> 0 only)
    open_folder_requested = pyqtSignal()
    recent_folder_requested = pyqtSignal(str)
    folder_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gallery_model = GalleryModel(self)
        self._loading = False
        self._density: str = "comfortable"
        self._excluded_rating_tags: list[str] = []
        self._pager: GalleryPager | None = None
        self._load_token: int = 0
        self._folder_loader_signals = FolderLoaderSignals()
        self._folder_loader_signals.rows_ready.connect(self._on_folder_loaded)
        self.setModel(self._gallery_model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(8)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)  # implies setDragEnabled(True)
        self.setStyleSheet("""
            QListView::item:hover {
                background: rgba(100, 150, 255, 0.12);
                border-radius: 4px;
            }
            QListView::item:selected {
                background: rgba(80, 130, 255, 0.25);
                border: 2px solid #4a90e2;
                border-radius: 4px;
            }
            QListView::item:selected:!active {
                background: rgba(80, 130, 255, 0.12);
                border: 2px solid #9ab8ef;
                border-radius: 4px;
            }
        """)
        self._apply_size(_compute_thumb_size(0))
        self.doubleClicked.connect(self._on_double_click)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._thumb_progress: tuple[int, int] = (0, 0)
        self._loading_flush_timer = QTimer(self)
        self._loading_flush_timer.setSingleShot(True)
        self._loading_flush_timer.setInterval(50)
        self._loading_flush_timer.timeout.connect(self._flush_thumb_progress)
        self._gallery_model._signals.progress.connect(self._on_thumb_progress_raw)
        self._gallery_model._signals.all_loaded.connect(self._on_all_loaded)
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # Viewport-driven lazy loading: connect scroll to _on_scroll
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Hover metadata card
        self._hover_card = QFrame(None, Qt.WindowType.ToolTip)
        self._hover_card.setMaximumWidth(300)
        self._hover_card.setStyleSheet(
            "QFrame { background: #1c1c1c; border: 1px solid rgba(255,255,255,0.18);"
            " border-radius: 8px; }"
            "QLabel { color: #e8e8e8; background: transparent; border: none; }"
        )
        _card_layout = QVBoxLayout(self._hover_card)
        _card_layout.setContentsMargins(10, 8, 10, 8)
        _card_layout.setSpacing(5)
        self._hover_name_label = QLabel()
        self._hover_name_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self._hover_size_label = QLabel()
        self._hover_size_label.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        self._hover_tags_section_label = QLabel("TAGS")
        self._hover_tags_section_label.setStyleSheet(
            "color: #767676; font-size: 10px; font-weight: 500; letter-spacing: 0.04em;"
        )
        self._hover_tags_label = QLabel()
        self._hover_tags_label.setWordWrap(True)
        self._hover_tags_label.setStyleSheet("font-size: 11px; color: #b8b8b8;")
        _card_layout.addWidget(self._hover_name_label)
        _card_layout.addWidget(self._hover_size_label)
        _card_layout.addSpacing(3)
        _card_layout.addWidget(self._hover_tags_section_label)
        _card_layout.addWidget(self._hover_tags_label)
        self._hover_card.hide()

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._show_hover_card)
        self._hover_row: int = -1
        self._hover_card_token: int = 0
        self._hover_global_pos = QPoint()
        self._hover_card_signals = _HoverCardSignals(self)
        self._hover_card_signals.ready.connect(self._on_hover_card_ready)

        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.verticalScrollBar().valueChanged.connect(self._hide_hover_card)

        # Empty-state overlay
        self._empty_overlay = _EmptyStateOverlay(self)
        self._empty_overlay.setGeometry(self.viewport().rect())
        self._empty_overlay.hide()

    # ------------------------------------------------------------------
    # Drag support
    # ------------------------------------------------------------------

    def startDrag(self, supported_actions):
        ids = self.get_selected_ids()
        if not ids:
            return
        mime = QMimeData()
        mime.setData(_MIME_IMAGE_IDS, QByteArray(json.dumps(ids).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Single image with loaded pixmap → use thumbnail; otherwise count badge
        if len(ids) == 1:
            item = self._gallery_model.get_item(self.selectedIndexes()[0].row())
            pix = item.get("display_pix") if item else None
            if pix and not pix.isNull():
                drag.setPixmap(pix.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
                drag.setHotSpot(QPoint(32, 32))
            else:
                drag.setPixmap(self._make_drag_badge(1))
                drag.setHotSpot(QPoint(22, 22))
        else:
            drag.setPixmap(self._make_drag_badge(len(ids)))
            drag.setHotSpot(QPoint(22, 22))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    def _make_drag_badge(self, count: int) -> QPixmap:
        pix = QPixmap(44, 44)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(80, 130, 255, 210))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 40, 40)
        p.setPen(QColor(255, 255, 255))
        font = p.font()
        font.setBold(True)
        font.setPointSize(13)
        p.setFont(font)
        label = str(count) if count <= 99 else "99+"
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, label)
        p.end()
        return pix

    # ------------------------------------------------------------------
    # Event filter (hover card)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == QEvent.Type.MouseMove:
                self._on_hover_move(event.pos(), event.globalPosition().toPoint())
            elif t == QEvent.Type.Leave:
                self._hide_hover_card()
            elif t == QEvent.Type.ToolTip:
                return True  # suppress native tooltip while hover card is active
        return super().eventFilter(obj, event)

    def _on_hover_move(self, local_pos: QPoint, global_pos: QPoint):
        self._hover_global_pos = global_pos
        index = self.indexAt(local_pos)
        if not index.isValid():
            self._hide_hover_card()
            return
        if index.row() == self._hover_row:
            if self._hover_card.isVisible():
                self._position_hover_card()
            return
        self._hover_row = index.row()
        self._hover_card.hide()
        self._hover_timer.start()

    def _show_hover_card(self):
        item = self._gallery_model.get_item(self._hover_row)
        if item is None:
            return
        path = item["path"]
        image_id = item["id"]
        self._hover_card_token += 1
        token = self._hover_card_token
        basename = os.path.basename(path)
        fm = self._hover_name_label.fontMetrics()
        self._hover_name_label.setText(fm.elidedText(basename, Qt.TextElideMode.ElideMiddle, 260))
        self._hover_name_label.setToolTip(basename)
        self._hover_size_label.setText("")
        self._hover_tags_section_label.hide()
        self._hover_tags_label.hide()
        QThreadPool.globalInstance().start(
            _HoverCardRunnable(image_id, path, token, self._hover_card_signals)
        )

    @pyqtSlot(int, str, list)
    def _on_hover_card_ready(self, token: int, size_str: str, tag_names: list):
        if token != self._hover_card_token:
            return
        self._hover_size_label.setText(size_str)
        if tag_names:
            self._hover_tags_label.setText(" · ".join(tag_names))
            self._hover_tags_section_label.show()
            self._hover_tags_label.show()
        else:
            self._hover_tags_section_label.hide()
            self._hover_tags_label.hide()
        self._hover_card.adjustSize()
        self._position_hover_card()
        self._hover_card.show()

    def _position_hover_card(self):
        gp = self._hover_global_pos
        screen = QApplication.screenAt(gp) or QApplication.primaryScreen()
        sg = screen.availableGeometry()
        w, h = self._hover_card.width(), self._hover_card.height()
        x = gp.x() + 16
        y = gp.y() + 16
        if x + w > sg.right():
            x = gp.x() - w - 8
        if y + h > sg.bottom():
            y = gp.y() - h - 8
        self._hover_card.move(x, y)

    def _hide_hover_card(self, *_args):
        self._hover_timer.stop()
        self._hover_row = -1
        self._hover_card_token += 1  # invalidate any in-flight runnables
        self._hover_card.hide()

    def _on_scroll(self, *_args):
        """Compute visible row range and request/evict thumbnails accordingly."""
        vp = self.viewport()
        top_index = self.indexAt(vp.rect().topLeft())
        bottom_index = self.indexAt(vp.rect().bottomRight())

        first = top_index.row() if top_index.isValid() else 0
        last = bottom_index.row() if bottom_index.isValid() else self._gallery_model.count() - 1
        if last < 0:
            return

        self._gallery_model.request_thumbnails(first, last)
        self._gallery_model._evict_offscreen(first, last)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._on_scroll()
        self._empty_overlay.setGeometry(self.viewport().rect())

    def _on_all_loaded(self):
        self._loading = False
        self.viewport().update()
        self.thumbnails_ready.emit(self._gallery_model.count())
        self._update_overlay_visibility()

    def _on_selection_changed(self, selected, deselected):
        self.selection_changed.emit(self.get_selected_ids())

    def _apply_size(self, thumb_px: int):
        label_px = max(20, thumb_px // 5)  # scales: 70px->20, 140px->28, 300px->60
        self.setIconSize(QSize(thumb_px, thumb_px))
        self.setGridSize(QSize(thumb_px + 16, thumb_px + label_px))
        self._gallery_model.set_display_size(thumb_px)
        QTimer.singleShot(0, self._on_scroll)  # retrigger lazy loading after size change

    def _refresh_size(self):
        count = self._gallery_model.count()
        self._apply_size(_compute_thumb_size(count))

    def _on_thumb_progress_raw(self, loaded: int, total: int):
        self._thumb_progress = (loaded, total)
        if loaded >= total:
            self._loading_flush_timer.stop()
            self._flush_thumb_progress()
        elif not self._loading_flush_timer.isActive():
            self._loading_flush_timer.start()

    def _flush_thumb_progress(self):
        self.thumbnails_loading.emit(*self._thumb_progress)

    def _load_rows(self, rows):
        self._hide_hover_card()
        self._loading_flush_timer.stop()
        self.clearSelection()
        self._loading = False
        self._pager = GalleryPager(rows)
        self._show_page(0)

    def _show_page(self, page: int):
        self._hide_hover_card()
        page_rows = self._pager.get_page(page)
        cfg = _DENSITY_CONFIG[self._density]
        raw = _compute_thumb_size(len(page_rows))
        px = max(60, min(400, round(raw * cfg.factor / 2) * 2))
        self.setSpacing(cfg.spacing)
        self._apply_size(px)
        self._gallery_model.set_images(page_rows)
        self.scrollToTop()
        if page_rows:
            QTimer.singleShot(0, self._on_scroll)
        self.page_changed.emit(
            self._pager.current_page,
            self._pager.page_count,
            self._pager.total,
        )

    def next_page(self):
        if self._pager and self._pager.current_page + 1 < self._pager.page_count:
            self._show_page(self._pager.current_page + 1)

    def prev_page(self):
        if self._pager and self._pager.current_page > 0:
            self._show_page(self._pager.current_page - 1)

    def set_density(self, mode: str):
        if mode == self._density:
            return
        self._density = mode
        if self._pager is not None and self._pager.total > 0:
            self._show_page(self._pager.current_page)

    def set_show_folder_origin(self, show: bool):
        """Show 'folder/filename' labels instead of just filenames."""
        self._gallery_model.set_show_folder_origin(show)

    def set_recent_folders(self, paths: list):
        """Pass a list of recently opened folder paths to the empty-state overlay."""
        self._empty_overlay.set_recent_folders(paths)

    def _update_overlay_visibility(self):
        show = self._gallery_model.rowCount() == 0 and not self._loading
        self._empty_overlay.setGeometry(self.viewport().rect())
        self._empty_overlay.setVisible(show)
        if show:
            self._empty_overlay.raise_()

    def set_rating_filter(self, excluded: list[str]):
        """Set which rating tags to hide. Pass [] to show everything."""
        self._excluded_rating_tags = excluded

    def _apply_rating_filter(self, rows) -> list:
        if not self._excluded_rating_tags:
            return rows
        ids = [r["id"] for r in rows]
        allowed_ids = set(db.filter_out_images_with_tags(ids, self._excluded_rating_tags))
        return [r for r in rows if r["id"] in allowed_ids]

    def load_folder(self, folder: str):
        self._loading = True
        self._empty_overlay.hide()
        self._gallery_model.set_show_folder_origin(False)
        self._load_token += 1
        token = self._load_token
        from src.core.thumbnail_cache import VIDEO_EXTENSIONS
        from src.core.image_scanner import SUPPORTED_EXTENSIONS
        media_exts = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS
        worker = FolderLoaderRunnable(folder, media_exts, token, self._folder_loader_signals)
        QThreadPool.globalInstance().start(worker)

    def _on_folder_loaded(self, rows: list, token: int, recovered: int = 0):
        if token != self._load_token:
            return  # stale — user navigated away before this finished
        self._load_rows(self._apply_rating_filter(rows))
        if recovered > 0:
            self.tags_recovered.emit(recovered)

    def load_images(self, rows, show_folder_origin: bool = False) -> LoadResult:
        self._gallery_model.set_show_folder_origin(show_folder_origin)
        valid_rows = [r for r in rows if os.path.isfile(r["path"])]
        filtered = self._apply_rating_filter(valid_rows)
        shown = len(filtered)
        sfw_hidden = len(valid_rows) - len(filtered)
        missing = len(rows) - len(valid_rows)
        self._load_rows(filtered)
        self._update_overlay_visibility()
        return LoadResult(shown, sfw_hidden, missing)

    def load_paths(self, paths: list[str]):
        self._gallery_model.set_show_folder_origin(False)
        valid = [p for p in paths if os.path.isfile(p)]
        rows, _recovered = db.get_or_create_images_batch(valid)
        self._load_rows(rows)
        self._update_overlay_visibility()

    def _on_double_click(self, index: QModelIndex):
        self._hide_hover_card()
        image_id = self._gallery_model.get_image_id(index.row())
        if image_id is not None:
            self.image_double_clicked.emit(image_id)

    def get_selected_ids(self) -> list[int]:
        ids = []
        for index in self.selectedIndexes():
            image_id = self._gallery_model.get_image_id(index.row())
            if image_id is not None:
                ids.append(image_id)
        return ids

    def _on_context_menu(self, pos):
        self._hide_hover_card()
        ids = self.get_selected_ids()
        if ids:
            self.context_menu_requested.emit(ids, self.viewport().mapToGlobal(pos))
        else:
            self.empty_context_menu_requested.emit(self.viewport().mapToGlobal(pos))

    def paintEvent(self, event):
        super().paintEvent(event)

    def image_count(self) -> int:
        return self._gallery_model.count()

    def get_all_items(self) -> list[tuple[int, str]]:
        if self._pager:
            return self._pager.all_items()
        return self._gallery_model.get_all_items()

    def remove_image(self, image_id: int):
        if self._pager:
            self._pager.remove(image_id)
            # Try to remove from model first (it may be on the current page)
            self._gallery_model.remove_image(image_id)
            self._refresh_size()
            # Update pagination controls
            self.page_changed.emit(
                self._pager.current_page,
                self._pager.page_count,
                self._pager.total,
            )
            if self._pager.total == 0:
                self._update_overlay_visibility()
            elif self._gallery_model.rowCount() == 0:
                # Current page emptied but pager has items on other pages — go to last valid page
                self._show_page(min(self._pager.current_page, self._pager.page_count - 1))
        else:
            self._gallery_model.remove_image(image_id)
            self._refresh_size()
            if self._gallery_model.rowCount() == 0:
                self._update_overlay_visibility()

    def mark_image_error(self, image_id: int):
        """Mark a thumbnail with a red error overlay (failed file operation)."""
        self._gallery_model.mark_error(image_id)
