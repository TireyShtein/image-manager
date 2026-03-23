import json
import os
from PyQt6.QtWidgets import (QListView, QAbstractItemView, QStyle, QFrame, QLabel,
                             QVBoxLayout, QApplication, QWidget, QPushButton)
from PyQt6.QtCore import (Qt, QAbstractListModel, QModelIndex, QSize,
                           QRunnable, QThreadPool, pyqtSignal, QObject, pyqtSlot,
                           QTimer, QPoint, QEvent, QByteArray, QMimeData)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QDrag
from src.core import thumbnail_cache, database as db
from typing import NamedTuple

# Maximum size stored in the thumbnail cache on disk
CACHE_THUMB_SIZE = 256

# Prefetch / eviction margins (in rows)
_PREFETCH_MARGIN = 40
_EVICT_MARGIN = 80

# Size tiers: (max_count, thumb_px)
_SIZE_TIERS = [
    (4,    300),
    (12,   240),
    (30,   180),
    (80,   140),
    (200,  110),
    (500,   85),
    (700,   78),
]
_MIN_THUMB_SIZE = 70


class _DensityConfig(NamedTuple):
    factor: float
    spacing: int


_DENSITY_CONFIG: dict[str, _DensityConfig] = {
    "compact":     _DensityConfig(factor=0.65, spacing=4),
    "comfortable": _DensityConfig(factor=1.0,  spacing=8),
    "spacious":    _DensityConfig(factor=1.40, spacing=12),
}

# Pagination
PAGE_SIZE = 200

# Drag-and-drop MIME type for image ID payloads
_MIME_IMAGE_IDS = "application/x-imagemanager-ids"


def _compute_thumb_size(count: int) -> int:
    for max_count, size in _SIZE_TIERS:
        if count <= max_count:
            return size
    return _MIN_THUMB_SIZE


# Reusable grey placeholder, created lazily
_placeholder_cache: dict[int, QPixmap] = {}


def _get_placeholder(size: int) -> QPixmap:
    if size not in _placeholder_cache:
        pix = QPixmap(size, size)
        pix.fill(QColor(220, 220, 220))
        _placeholder_cache[size] = pix
    return _placeholder_cache[size]

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


class LoadResult(NamedTuple):
    shown: int
    sfw_hidden: int        # files on disk, filtered out by SFW Mode
    missing: int           # files not found on disk at all

class GalleryPager:
    """Holds all rows (lightweight dicts) and serves fixed-size pages."""
    def __init__(self, rows: list):
        # Store as plain dicts to avoid holding sqlite3.Row refs across pages
        self._rows = [{"id": r["id"], "path": r["path"]} for r in rows]
        self._page = 0

    @property
    def total(self) -> int:
        return len(self._rows)

    @property
    def page_count(self) -> int:
        return max(1, (len(self._rows) + PAGE_SIZE - 1) // PAGE_SIZE)

    @property
    def current_page(self) -> int:
        return self._page

    def get_page(self, page: int) -> list[dict]:
        self._page = max(0, min(page, self.page_count - 1))
        start = self._page * PAGE_SIZE
        return self._rows[start:start + PAGE_SIZE]

    def remove(self, image_id: int):
        self._rows = [r for r in self._rows if r["id"] != image_id]
        # Clamp page in case we removed the last item on the last page
        self._page = min(self._page, self.page_count - 1)

    def all_items(self) -> list[tuple[int, str]]:
        return [(r["id"], r["path"]) for r in self._rows]


class ThumbnailSignals(QObject):
    loaded = pyqtSignal(int, str, int)  # (image_id, thumb_path, token)
    progress = pyqtSignal(int, int)     # (loaded_count, total_count)
    all_loaded = pyqtSignal()


class ThumbnailLoader(QRunnable):
    def __init__(self, image_id: int, image_path: str, signals: ThumbnailSignals, token: int):
        super().__init__()
        self.image_id = image_id
        self.image_path = image_path
        self.signals = signals
        self._token = token
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        try:
            thumb = thumbnail_cache.get_or_create_thumbnail(self.image_path)
        except Exception:
            thumb = None
        # Always emit so the counter increments even on failure
        self.signals.loaded.emit(self.image_id, thumb or "", self._token)


class FolderLoaderSignals(QObject):
    rows_ready = pyqtSignal(list, int, int)  # (rows, token, recovered_count)


class FolderLoaderRunnable(QRunnable):
    def __init__(self, folder: str, media_exts: set, token: int, signals: FolderLoaderSignals):
        super().__init__()
        self._folder = folder
        self._media_exts = media_exts
        self._token = token
        self._signals = signals
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        paths = []
        try:
            with os.scandir(self._folder) as entries:
                for entry in entries:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in self._media_exts:
                        paths.append(entry.path)
        except OSError:
            pass
        sorted_paths = sorted(paths, key=lambda p: os.path.basename(p).lower())
        rows, recovered = db.get_or_create_images_batch(sorted_paths)
        self._signals.rows_ready.emit(rows, self._token, recovered)


class GalleryModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Each item: {"id", "path", "display_pix": QPixmap|None}
        self._items: list[dict] = []
        self._id_index: dict[int, int] = {}
        self._display_size: int = _compute_thumb_size(0)
        self._pool = QThreadPool.globalInstance()
        self._signals = ThumbnailSignals()
        self._signals.loaded.connect(self._on_thumbnail_loaded)
        self._total: int = 0
        self._loaded: int = 0
        self._thumb_token: int = 0
        self._show_folder_origin: bool = False
        # Track which rows have been queued for thumbnail loading
        self._queued: set[int] = set()
        self._error_ids: set[int] = set()

    def set_images(self, rows):
        self.beginResetModel()
        self._items = [{"id": r["id"], "path": r["path"],
                        "display_pix": None} for r in rows]
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        self._total = len(self._items)
        self._loaded = 0
        self._queued = set()
        self._error_ids = set()
        self._thumb_token += 1
        self.endResetModel()
        if self._total == 0:
            self._signals.all_loaded.emit()
        # Do NOT call _start_loading() — thumbnails are loaded lazily via
        # request_thumbnails() driven by the viewport scroll position.

    def get_all_items(self) -> list[tuple[int, str]]:
        return [(item["id"], item["path"]) for item in self._items]

    def set_display_size(self, size: int):
        if size == self._display_size:
            return
        self._display_size = size
        # Without source_pix we cannot re-scale in memory.  Clear all
        # display pixmaps and let the lazy loader refetch from disk cache.
        for item in self._items:
            item["display_pix"] = None
        self._queued = set()
        if self._items:
            self._loaded = 0
            self._total = len(self._items)
            self.dataChanged.emit(
                self.index(0),
                self.index(len(self._items) - 1),
                [Qt.ItemDataRole.DecorationRole]
            )

    def _scale(self, pix: QPixmap) -> QPixmap:
        return pix.scaled(
            self._display_size, self._display_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

    def request_thumbnails(self, first_row: int, last_row: int):
        """Queue thumbnail loading for rows in the visible range + margin.

        Only queues items that have no display_pix and are not already queued.
        """
        start = max(0, first_row - _PREFETCH_MARGIN)
        end = min(len(self._items), last_row + _PREFETCH_MARGIN + 1)
        for i in range(start, end):
            if i in self._queued:
                continue
            item = self._items[i]
            if item["display_pix"] is not None:
                continue
            self._queued.add(i)
            loader = ThumbnailLoader(item["id"], item["path"], self._signals, self._thumb_token)
            self._pool.start(loader)

    def _evict_offscreen(self, first_visible: int, last_visible: int):
        """Free pixmap memory for rows far outside the visible range."""
        keep_start = max(0, first_visible - _EVICT_MARGIN)
        keep_end = min(len(self._items), last_visible + _EVICT_MARGIN + 1)
        for i in range(0, keep_start):
            item = self._items[i]
            if item["display_pix"] is not None:
                item["display_pix"] = None
                self._queued.discard(i)
        for i in range(keep_end, len(self._items)):
            item = self._items[i]
            if item["display_pix"] is not None:
                item["display_pix"] = None
                self._queued.discard(i)

    def _on_thumbnail_loaded(self, image_id: int, thumb_path: str, token: int):
        if token != self._thumb_token:
            return  # stale result from a previous set_images() batch
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        # If the item was evicted before the loader finished, discard the result
        if idx not in self._queued:
            return
        if thumb_path:
            source = QPixmap(thumb_path)
            self._items[idx]["display_pix"] = self._scale(source)
            index = self.index(idx)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
        self._loaded += 1
        self._signals.progress.emit(self._loaded, self._total)
        if self._loaded >= self._total:
            self._signals.all_loaded.emit()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                Qt.ItemFlag.ItemIsDragEnabled)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            pix = item["display_pix"] if item["display_pix"] is not None else _get_placeholder(self._display_size)
            if item["id"] in self._error_ids:
                pix = self._apply_error_overlay(pix)
            return pix
        if role == Qt.ItemDataRole.DisplayRole:
            if self._display_size < 140:
                return None
            if self._show_folder_origin:
                folder = os.path.basename(os.path.dirname(item["path"]))
                return f"{folder}/{os.path.basename(item['path'])}"
            return os.path.basename(item["path"])
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = item["path"]
            if item["id"] in self._error_ids:
                tip += "\n[File operation failed]"
            return tip
        if role == Qt.ItemDataRole.UserRole:
            return item["id"]
        return None

    def get_image_id(self, row: int) -> int | None:
        if 0 <= row < len(self._items):
            return self._items[row]["id"]
        return None

    def get_item(self, row: int) -> dict | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def remove_image(self, image_id: int):
        idx = self._id_index.get(image_id)
        if idx is None:
            return
        was_loaded = self._items[idx]["display_pix"] is not None
        self.beginRemoveRows(QModelIndex(), idx, idx)
        self._items.pop(idx)
        self._queued.discard(idx)
        # Rebuild index and queued set (row numbers shifted)
        self._id_index = {item["id"]: i for i, item in enumerate(self._items)}
        new_queued = set()
        for q in self._queued:
            if q > idx:
                new_queued.add(q - 1)
            else:
                new_queued.add(q)
        self._queued = new_queued
        self.endRemoveRows()
        self._error_ids.discard(image_id)
        # Adjust counters so all_loaded can still fire after removals
        self._total = max(0, self._total - 1)
        if was_loaded:
            self._loaded = max(0, self._loaded - 1)
        if self._loaded >= self._total:
            self._signals.all_loaded.emit()

    def mark_error(self, image_id: int):
        """Mark a thumbnail with a red error overlay (failed file operation)."""
        self._error_ids.add(image_id)
        idx = self._id_index.get(image_id)
        if idx is not None:
            index = self.index(idx)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def _apply_error_overlay(self, pix: QPixmap) -> QPixmap:
        result = QPixmap(pix)
        painter = QPainter(result)
        painter.fillRect(result.rect(), QColor(220, 40, 40, 70))
        painter.setPen(QPen(QColor(220, 40, 40, 180), 2))
        painter.drawRect(1, 1, result.width() - 2, result.height() - 2)
        painter.setPen(QColor(255, 255, 255))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.fillRect(result.width() - 18, 2, 16, 16, QColor(220, 40, 40, 220))
        painter.drawText(result.width() - 18, 2, 16, 16, Qt.AlignmentFlag.AlignCenter, "!")
        painter.end()
        return result

    def set_show_folder_origin(self, show: bool):
        if show == self._show_folder_origin:
            return
        self._show_folder_origin = show
        if self._items:
            self.dataChanged.emit(self.index(0), self.index(len(self._items) - 1),
                                  [Qt.ItemDataRole.DisplayRole])

    def count(self) -> int:
        return len(self._items)


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
        self._hover_global_pos = QPoint()

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
        basename = os.path.basename(path)
        fm = self._hover_name_label.fontMetrics()
        self._hover_name_label.setText(fm.elidedText(basename, Qt.TextElideMode.ElideMiddle, 260))
        self._hover_name_label.setToolTip(basename)
        try:
            size_bytes = os.path.getsize(path)
            if size_bytes >= 1_048_576:
                size_str = f"{size_bytes / 1_048_576:.1f} MB"
            elif size_bytes >= 1024:
                size_str = f"{size_bytes / 1024:.0f} KB"
            else:
                size_str = f"{size_bytes} B"
        except OSError:
            size_str = "—"
        self._hover_size_label.setText(size_str)
        tag_rows = db.get_tags_for_images([item["id"]])
        if tag_rows:
            self._hover_tags_label.setText(" · ".join(r[0] for r in tag_rows))
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
