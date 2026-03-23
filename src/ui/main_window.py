import os
import json
from PyQt6.QtWidgets import (QMainWindow, QPushButton, QWidget, QHBoxLayout, QVBoxLayout,
                              QSplitter, QStatusBar, QProgressBar, QLabel,
                              QFileDialog, QMessageBox, QInputDialog, QMenu,
                              QApplication, QDialog, QStyle)
from PyQt6.QtCore import Qt, QThread, QSettings, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from src.ui.folder_tree import FolderTree
from src.ui.gallery_view import GalleryView
from src.ui.image_viewer import ImageViewer, TriageImageViewer, VIDEO_EXTENSIONS
from src.ui.tag_panel import TagPanel
from src.ui.album_panel import AlbumPanel
from src.core import database as db, image_scanner, file_ops
from src.ai.wd14_worker import WD14Worker
from src.ai.rating_sort_worker import RatingSortWorker

_CHIP_STYLE = (
    "QPushButton { color: #dde; background: rgba(80,130,255,0.20);"
    " border: 1px solid rgba(80,130,255,0.45); border-radius: 3px;"
    " padding: 1px 7px; font-size: 11px; }"
    "QPushButton:hover { background: rgba(80,130,255,0.35); }"
)


class ScanWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_scan = pyqtSignal(int)  # number of images added

    def __init__(self, folder: str, parent=None):
        super().__init__(parent)
        self._folder = folder

    def run(self):
        def cb(current, total):
            self.progress.emit(current, total)
        added = image_scanner.scan_folder(self._folder, cb)
        self.finished_scan.emit(added)


class FileOpWorker(QThread):
    progress = pyqtSignal(int, int)
    item_done = pyqtSignal(int)            # image_id
    item_error = pyqtSignal(int, str)
    finished_op = pyqtSignal(int, list)    # (success_count, error_msgs)

    def __init__(self, op: str, image_ids: list, dest: str, parent=None):
        super().__init__(parent)
        self._op = op
        self._image_ids = image_ids
        self._dest = dest

    def run(self):
        errors, success = [], 0
        total = len(self._image_ids)
        for i, image_id in enumerate(self._image_ids):
            try:
                if self._op == "move":
                    file_ops.move_image(image_id, self._dest)
                else:
                    file_ops.copy_image(image_id, self._dest)
                success += 1
                self.item_done.emit(image_id)
            except Exception as e:
                errors.append(str(e))
                self.item_error.emit(image_id, str(e))
            self.progress.emit(i + 1, total)
        self.finished_op.emit(success, errors)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Manager")
        self.resize(1280, 800)
        self._current_folder: str | None = None
        self._active_tag_filter: list[str] = []
        self._active_tag_mode: str = "AND"
        self._active_album_id: int | None = None
        self._active_collection_id: int | None = None
        self._wd14_worker: WD14Worker | None = None
        self._rating_sort_worker: RatingSortWorker | None = None
        self._scan_worker: ScanWorker | None = None
        self._file_op_worker: FileOpWorker | None = None
        self._settings = QSettings("ImageManager", "ImageManager")
        self._sfw_mode: bool = self._settings.value("sfw_mode", False, type=bool)
        self._status_prefix = "Ready"
        self._gallery_total: int = 0
        _db_is_new = not db.db_exists()
        db.init_db()
        if _db_is_new:
            print("[DB] Created new database at:", db.DB_PATH)
        else:
            print("[DB] Opened existing database at:", db.DB_PATH)
        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        if self._sfw_mode:
            self._gallery.set_rating_filter(["rating:explicit", "rating:questionable"])
            self._tag_panel.set_sfw_mode(True)

        # Debounced tag-panel refresh for AI signal handlers
        self._tag_refresh_timer = QTimer(self)
        self._tag_refresh_timer.setSingleShot(True)
        self._tag_refresh_timer.setInterval(500)
        self._tag_refresh_timer.timeout.connect(self._tag_panel.refresh)

        self._gallery.set_density(self._settings.value("density", "comfortable"))
        _recent = self._settings.value("recent_folders", [])
        if not isinstance(_recent, list):
            _recent = [_recent] if isinstance(_recent, str) and _recent else []
        self._gallery.set_recent_folders(_recent)
        self._restore_last_folder()
        self._tag_panel.refresh()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(self._splitter)

        # Left: go-up button + folder tree
        self._left_panel = QWidget()
        self._left_panel.setMinimumWidth(180)
        self._left_panel.setMaximumWidth(450)
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._btn_go_up = QPushButton("↑ Go Up")
        self._btn_go_up.setFixedHeight(26)
        self._btn_go_up.setEnabled(False)
        self._btn_go_up.clicked.connect(self._go_up_folder)
        left_layout.addWidget(self._btn_go_up)

        self._folder_tree = FolderTree()
        self._folder_tree.folder_selected.connect(self._on_folder_selected)
        self._folder_tree.files_selected.connect(self._on_tree_files_selected)
        self._folder_tree.images_dropped_on_folder.connect(self._on_dnd_folder_drop)
        left_layout.addWidget(self._folder_tree)
        self._splitter.addWidget(self._left_panel)

        # Centre: filter chip bar + gallery + pagination bar
        gallery_container = QWidget()
        gallery_layout = QVBoxLayout(gallery_container)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)

        # Filter chip bar — shown when a tag filter is active
        self._filter_chip_bar = QWidget()
        self._filter_chip_bar.setFixedHeight(30)
        self._filter_chip_bar.setStyleSheet(
            "background: rgba(255,255,255,0.04); border-bottom: 1px solid rgba(255,255,255,0.08);"
        )
        self._chip_layout = QHBoxLayout(self._filter_chip_bar)
        self._chip_layout.setContentsMargins(8, 3, 8, 3)
        self._chip_layout.setSpacing(5)
        self._chip_layout.addStretch()
        self._filter_chip_bar.setVisible(False)
        gallery_layout.addWidget(self._filter_chip_bar)

        self._gallery = GalleryView()
        self._gallery.image_double_clicked.connect(self._on_image_double_clicked)
        self._gallery.context_menu_requested.connect(self._on_context_menu)
        self._gallery.selection_changed.connect(self._on_selection_changed)
        self._gallery.thumbnails_loading.connect(self._on_thumbnails_loading)
        self._gallery.thumbnails_ready.connect(self._on_thumbnails_ready)
        self._gallery.empty_context_menu_requested.connect(self._on_empty_gallery_context_menu)
        self._gallery.page_changed.connect(self._on_page_changed)
        self._gallery.tags_recovered.connect(self._on_tags_recovered)
        self._gallery.open_folder_requested.connect(self._open_folder)
        self._gallery.recent_folder_requested.connect(self._open_recent_folder)
        self._gallery.folder_dropped.connect(self._open_recent_folder)
        gallery_layout.addWidget(self._gallery, 1)

        # Pagination bar
        self._page_bar = QWidget()
        page_layout = QHBoxLayout(self._page_bar)
        page_layout.setContentsMargins(4, 3, 4, 3)
        self._btn_prev_page = QPushButton("◀ Prev")
        self._btn_prev_page.setFixedWidth(80)
        self._btn_prev_page.clicked.connect(self._gallery.prev_page)
        self._btn_next_page = QPushButton("Next ▶")
        self._btn_next_page.setFixedWidth(80)
        self._btn_next_page.clicked.connect(self._gallery.next_page)
        self._page_label = QLabel("")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_layout.addWidget(self._btn_prev_page)
        page_layout.addStretch()
        page_layout.addWidget(self._page_label)
        page_layout.addStretch()
        page_layout.addWidget(self._btn_next_page)
        self._page_bar.setVisible(False)
        gallery_layout.addWidget(self._page_bar)

        self._splitter.addWidget(gallery_container)

        # Right: tag panel only (album panel moved to floating dialog)
        self._right_panel = QWidget()
        self._right_panel.setMinimumWidth(180)
        self._right_panel.setMaximumWidth(400)
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._tag_panel = TagPanel()
        self._tag_panel.tag_filter_changed.connect(self._on_tag_filter)
        right_layout.addWidget(self._tag_panel)

        self._splitter.addWidget(self._right_panel)
        self._splitter.setStretchFactor(1, 1)

        # Collapsible: left and right only; center gallery is never collapsible
        self._splitter.setCollapsible(0, True)
        self._splitter.setCollapsible(1, False)
        self._splitter.setCollapsible(2, True)

        # Restore splitter state from previous session
        saved = self._settings.value("splitter_state")
        if saved is None or not self._splitter.restoreState(saved):
            self._splitter.setSizes([220, 9999, 240])

        left_vis = self._settings.value("left_panel_visible", True, type=bool)
        right_vis = self._settings.value("right_panel_visible", True, type=bool)
        self._left_panel.setVisible(left_vis)
        self._right_panel.setVisible(right_vis)

        # Album panel: instantiated but shown on demand as a floating dialog
        self._album_panel = AlbumPanel()
        self._album_panel.album_selected.connect(self._on_album_selected)
        self._album_panel.collection_selected.connect(self._on_collection_selected)
        self._album_panel.images_added_to_album.connect(self._on_images_added_to_album)
        self._album_dialog: QDialog | None = None

    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("File")
        act_open = QAction("Open Folder…", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self._open_folder)
        file_menu.addAction(act_open)

        act_scan = QAction("Scan Folder into Library", self)
        act_scan.triggered.connect(self._scan_folder)
        file_menu.addAction(act_scan)

        act_cleanup = QAction("Clean Up Missing Files from Library", self)
        act_cleanup.triggered.connect(self._cleanup_library)
        file_menu.addAction(act_cleanup)
        file_menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        view_menu = mb.addMenu("View")
        self._act_sfw = QAction("SFW Mode", self)
        self._act_sfw.setCheckable(True)
        self._act_sfw.setChecked(self._sfw_mode)
        self._act_sfw.triggered.connect(self._on_sfw_toggle)
        view_menu.addAction(self._act_sfw)
        view_menu.addSeparator()

        density_group = QActionGroup(self)
        density_group.setExclusive(True)
        saved_density = self._settings.value("density", "comfortable")
        density_menu = view_menu.addMenu("Density")
        for label, key in [("Compact", "compact"), ("Comfortable", "comfortable"), ("Spacious", "spacious")]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setData(key)
            act.setChecked(key == saved_density)
            density_group.addAction(act)
            density_menu.addAction(act)
        density_group.triggered.connect(
            lambda a: (
                self._gallery.set_density(a.data()),
                self._settings.setValue("density", a.data()),
            )
        )
        view_menu.addSeparator()

        self._act_show_tree = QAction("Show Folder Tree", self)
        self._act_show_tree.setShortcut("Ctrl+1")
        self._act_show_tree.setCheckable(True)
        self._act_show_tree.setChecked(self._left_panel.isVisible())
        self._act_show_tree.toggled.connect(self._toggle_left_panel)
        view_menu.addAction(self._act_show_tree)

        self._act_show_tags = QAction("Show Tag Panel", self)
        self._act_show_tags.setShortcut("Ctrl+2")
        self._act_show_tags.setCheckable(True)
        self._act_show_tags.setChecked(self._right_panel.isVisible())
        self._act_show_tags.toggled.connect(self._toggle_right_panel)
        view_menu.addAction(self._act_show_tags)
        view_menu.addSeparator()

        act_albums = QAction("Albums…", self)
        act_albums.triggered.connect(self._show_album_dialog)
        view_menu.addAction(act_albums)

        self._act_save_collection = QAction("Save Filter as Collection…", self)
        self._act_save_collection.setEnabled(False)
        self._act_save_collection.triggered.connect(self._save_current_filter_as_collection)
        view_menu.addAction(self._act_save_collection)

        view_menu.addSeparator()
        self._act_triage = QAction("Triage Mode…", self)
        self._act_triage.setShortcut("Ctrl+K")
        self._act_triage.triggered.connect(self._open_triage_mode)
        view_menu.addAction(self._act_triage)

        self._splitter.splitterMoved.connect(self._on_splitter_moved)

        ai_menu = mb.addMenu("AI")
        self._act_wd14 = QAction("Tag with WD14…", self)
        self._act_wd14.setShortcut("Ctrl+T")
        self._act_wd14.triggered.connect(self._run_wd14_tagging)
        ai_menu.addAction(self._act_wd14)

        self._act_cancel_wd14 = QAction("Cancel WD14 Tagging", self)
        self._act_cancel_wd14.setEnabled(False)
        self._act_cancel_wd14.triggered.connect(self._cancel_wd14_tagging)
        ai_menu.addAction(self._act_cancel_wd14)

        ai_menu.addSeparator()
        self._act_sort = QAction("Sort into SFW/NSFW by Tags…", self)
        self._act_sort.triggered.connect(self._run_rating_sort)
        ai_menu.addAction(self._act_sort)

        self._act_cancel_sort = QAction("Cancel Sort", self)
        self._act_cancel_sort.setEnabled(False)
        self._act_cancel_sort.triggered.connect(self._cancel_rating_sort)
        ai_menu.addAction(self._act_cancel_sort)

    def _update_filter_chips(self, tag_names: list[str], mode: str = "AND"):
        """Rebuild the filter chip bar. Pass empty list to hide it."""
        layout = self._chip_layout
        # Remove all widgets except the trailing stretch
        while layout.count() > 1:
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if not tag_names:
            self._filter_chip_bar.setVisible(False)
            return
        # Mode label
        mode_label = QLabel(mode)
        mode_label.setStyleSheet(
            "QLabel { color: #aaa; font-size: 11px; font-weight: bold;"
            " border: 1px solid rgba(255,255,255,0.2); border-radius: 3px; padding: 1px 5px; }"
        )
        layout.insertWidget(0, mode_label)
        for i, name in enumerate(tag_names):
            btn = QPushButton(f"{name}  ×")
            btn.setStyleSheet(_CHIP_STYLE)
            btn.setToolTip(f"Remove '{name}' from filter")
            btn.clicked.connect(lambda checked, n=name: self._remove_filter_chip(n))
            layout.insertWidget(i + 1, btn)
        self._filter_chip_bar.setVisible(True)

    def _remove_filter_chip(self, tag_name: str):
        self._tag_panel.remove_filter_tag(tag_name)

    def _toggle_left_panel(self, visible: bool):
        self._left_panel.setVisible(visible)
        if visible and self._splitter.sizes()[0] == 0:
            sizes = self._splitter.sizes()
            sizes[0] = 220
            sizes[1] = max(sizes[1] - 220, 200)
            self._splitter.setSizes(sizes)

    def _toggle_right_panel(self, visible: bool):
        self._right_panel.setVisible(visible)
        if visible and self._splitter.sizes()[2] == 0:
            sizes = self._splitter.sizes()
            sizes[2] = 240
            sizes[1] = max(sizes[1] - 240, 200)
            self._splitter.setSizes(sizes)

    def _on_splitter_moved(self, pos: int, index: int):
        sizes = self._splitter.sizes()
        left_vis = sizes[0] > 0
        right_vis = sizes[2] > 0
        self._left_panel.setVisible(left_vis)
        self._right_panel.setVisible(right_vis)
        self._act_show_tree.blockSignals(True)
        self._act_show_tree.setChecked(left_vis)
        self._act_show_tree.blockSignals(False)
        self._act_show_tags.blockSignals(True)
        self._act_show_tags.setChecked(right_vis)
        self._act_show_tags.blockSignals(False)

    def _set_counter_progress_visible(self, visible: bool):
        self._progress_counter.setVisible(visible)
        self._progress.setVisible(visible)

    def _build_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_label = QLabel("Ready")
        self._statusbar.addWidget(self._status_label, 1)

        self._selected_label = QLabel("")
        self._statusbar.addWidget(self._selected_label)

        self._sfw_indicator = QLabel(" SFW ")
        self._sfw_indicator.setStyleSheet(
            "QLabel { color: #fff; background: #b45309; border-radius: 3px;"
            " padding: 1px 5px; font-size: 11px; font-weight: bold; }"
        )
        self._sfw_indicator.setToolTip("SFW Mode is active — explicit/questionable images are hidden")
        self._sfw_indicator.setVisible(self._sfw_mode)
        self._statusbar.addPermanentWidget(self._sfw_indicator)

        self._progress_counter = QLabel("")
        self._progress_counter.setFixedWidth(80)
        self._progress_counter.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._progress_counter.setStyleSheet("color: #ffffff;")
        self._progress_counter.setVisible(False)
        self._statusbar.addPermanentWidget(self._progress_counter)

        self._progress = QProgressBar()
        self._progress.setFixedWidth(200)
        self._progress.setVisible(False)
        self._statusbar.addPermanentWidget(self._progress)

    # ------------------------------------------------------------------ Slots

    def _restore_last_folder(self):
        folder = self._settings.value("last_folder", "")
        if folder and os.path.isdir(folder):
            self._current_folder = folder
            self._folder_tree.set_root(folder)
            self._status_prefix = f"Folder: {os.path.basename(folder)}"
            self._status_label.setToolTip(folder)
            self._gallery.load_folder(folder)
            self._status_label.setText(self._status_prefix)
            self._update_go_up_button()
            self._add_recent_folder(folder)

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            self._current_folder = folder
            self._active_tag_filter = []
            self._active_album_id = None
            self._active_collection_id = None
            self._status_prefix = f"Folder: {os.path.basename(folder)}"
            self._status_label.setToolTip(folder)
            self._gallery.load_folder(folder)
            self._folder_tree.set_root(folder)
            self._status_label.setText(self._status_prefix)
            self._settings.setValue("last_folder", folder)
            self._add_recent_folder(folder)
            self._tag_panel.clear_search()
            self._update_filter_chips([])
            self._update_go_up_button()

    def _go_up_folder(self):
        if not self._current_folder:
            return
        parent = os.path.dirname(self._current_folder)
        if not parent or parent == self._current_folder:
            return
        self._current_folder = parent
        self._active_tag_filter = []
        self._active_album_id = None
        self._active_collection_id = None
        self._status_prefix = f"Folder: {os.path.basename(parent)}"
        self._status_label.setToolTip(parent)
        self._folder_tree.set_root(parent)
        self._gallery.load_folder(parent)
        self._status_label.setText(self._status_prefix)
        self._settings.setValue("last_folder", parent)
        self._add_recent_folder(parent)
        self._tag_panel.clear_search()
        self._update_filter_chips([])
        self._update_go_up_button()

    def _update_go_up_button(self):
        if self._current_folder:
            parent = os.path.dirname(self._current_folder)
            self._btn_go_up.setEnabled(bool(parent) and parent != self._current_folder)
        else:
            self._btn_go_up.setEnabled(False)

    def _add_recent_folder(self, path: str):
        val = self._settings.value("recent_folders", [])
        if not isinstance(val, list):
            val = [val] if isinstance(val, str) and val else []
        recent = [p for p in val if p != path]
        recent.insert(0, path)
        # Prune stale entries (deleted/unmounted paths) and cap at 5
        recent = [p for p in recent if os.path.isdir(p)][:5]
        self._settings.setValue("recent_folders", recent)
        self._gallery.set_recent_folders(recent)

    def _open_recent_folder(self, path: str):
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Folder Not Found",
                                f"The folder no longer exists:\n{path}")
            return
        self._current_folder = path
        self._active_tag_filter = []
        self._active_album_id = None
        self._active_collection_id = None
        self._status_prefix = f"Folder: {os.path.basename(path)}"
        self._status_label.setToolTip(path)
        self._folder_tree.set_root(path)
        self._gallery.load_folder(path)
        self._status_label.setText(self._status_prefix)
        self._settings.setValue("last_folder", path)
        self._add_recent_folder(path)
        self._tag_panel.clear_search()
        self._update_filter_chips([])
        self._update_go_up_button()

    def _cleanup_library(self):
        removed = db.cleanup_stale_images()
        self._tag_panel.refresh()
        QMessageBox.information(
            self, "Clean Up Library",
            f"Removed {removed} missing file(s) from the library."
            if removed else "No missing files found — library is clean."
        )

    def _scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Scan Folder into Library")
        if not folder:
            return
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self._status_label.setText(f"Scanning {folder}…")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._scan_worker = ScanWorker(folder, self)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished_scan.connect(lambda added: self._on_scan_finished(added, folder))
        self._scan_worker.start()

    def _on_scan_progress(self, current: int, total: int):
        if total:
            self._progress.setRange(0, total)
            self._progress.setValue(current)

    def _on_scan_finished(self, added: int, folder: str):
        self._progress.setVisible(False)
        self._status_label.setText(f"Scanned: {added} new images added from {folder}")
        if self._current_folder == folder:
            self._gallery.load_folder(folder)

    def _on_folder_selected(self, folder: str):
        self._current_folder = folder
        self._active_tag_filter = []
        self._active_album_id = None
        self._active_collection_id = None
        self._status_prefix = f"Folder: {os.path.basename(folder)}"
        self._status_label.setToolTip(folder)
        self._gallery.load_folder(folder)
        self._status_label.setText(self._status_prefix)
        self._settings.setValue("last_folder", folder)
        self._add_recent_folder(folder)
        self._tag_panel.clear_search()
        self._update_filter_chips([])
        self._update_go_up_button()

    def _open_location_in_tree(self, image_ids: list[int]):
        if not image_ids:
            return
        row = db.get_image(image_ids[0])
        if not row:
            return
        parent_dir = os.path.dirname(row["path"])
        if not os.path.isdir(parent_dir):
            return
        self._current_folder = parent_dir
        self._active_tag_filter = []
        self._active_album_id = None
        self._active_collection_id = None
        self._status_prefix = f"Folder: {os.path.basename(parent_dir)}"
        self._status_label.setToolTip(parent_dir)
        self._folder_tree.set_root(parent_dir)
        self._folder_tree.navigate_to(parent_dir)
        self._gallery.load_folder(parent_dir)
        self._status_label.setText(self._status_prefix)
        self._settings.setValue("last_folder", parent_dir)
        self._add_recent_folder(parent_dir)
        self._tag_panel.clear_search()
        self._update_filter_chips([])
        self._update_go_up_button()
        # Highlight the revealed image(s) in the tree after the model loads
        paths = []
        for iid in image_ids:
            r = db.get_image(iid)
            if r and os.path.dirname(r["path"]) == parent_dir:
                paths.append(r["path"])
        if paths:
            self._folder_tree.select_files(paths)

    def _on_tree_files_selected(self, paths: list[str]):
        self._gallery.load_paths(paths)
        self._status_label.setText(f"{len(paths)} file(s) selected in tree")
        self._tag_panel.clear_search()
        self._update_filter_chips([])

    def _on_thumbnails_loading(self, loaded: int, total: int):
        self._status_label.setText(f"{self._status_prefix} — Loading {loaded}/{total}…")

    def _on_thumbnails_ready(self, count: int):
        total = self._gallery_total if self._gallery_total else count
        if self._status_prefix.startswith("Folder:"):
            self._status_label.setText(f"{self._status_prefix} ({total} images)")
        else:
            self._status_label.setText(self._status_prefix)

    def _on_page_changed(self, page: int, page_count: int, total: int):
        self._gallery_total = total
        visible = page_count > 1
        self._page_bar.setVisible(visible)
        if visible:
            self._page_label.setText(f"Page {page + 1} of {page_count}  ({total} total)")
            self._btn_prev_page.setEnabled(page > 0)
            self._btn_next_page.setEnabled(page < page_count - 1)

    def _on_tags_recovered(self, count: int):
        if count > 0:
            label = "image" if count == 1 else "images"
            current = self._status_label.text()
            self._status_label.setText(f"{current} | Recovered tags for {count} moved {label}")
            self._tag_panel.refresh()

    def _make_image_nav_list(self) -> list[tuple[int, str]]:
        return [(iid, p) for iid, p in self._gallery.get_all_items()
                if os.path.splitext(p)[1].lower() not in VIDEO_EXTENSIONS]

    def _on_image_double_clicked(self, image_id: int):
        row = db.get_image(image_id)
        if not row:
            return
        nav = self._make_image_nav_list()
        idx = next((i for i, (iid, _) in enumerate(nav) if iid == image_id), 0)
        ImageViewer(image_id, row["path"], self, all_images=nav, current_index=idx).exec()

    def _on_selection_changed(self, ids: list):
        self._tag_panel.set_selected_images(ids)
        self._album_panel.set_selected_images(ids)
        count = len(ids)
        self._selected_label.setText(f"{count} selected" if count > 0 else "")

    def _on_tag_filter(self, tag_names: list[str], mode: str):
        self._active_tag_filter = tag_names
        self._active_tag_mode = mode
        self._active_album_id = None
        self._active_collection_id = None
        self._act_save_collection.setEnabled(bool(tag_names))
        self._update_filter_chips(tag_names, mode)
        if tag_names:
            rows = (db.get_images_by_tags_and(tag_names)
                    if mode == "AND" else db.get_images_by_tags_or(tag_names))
            connector = f" {mode} "
            label = (connector.join(tag_names) if len(tag_names) <= 2
                     else f"{tag_names[0]} {mode} +{len(tag_names) - 1} more")
            loaded_images = self._gallery.load_images(rows, show_folder_origin=True)
            parts = []
            if loaded_images.sfw_hidden > 0:
                parts.append(f"{loaded_images.sfw_hidden} hidden by SFW Mode")
            if loaded_images.missing > 0:
                parts.append(f"{loaded_images.missing} missing from disk")
            suffix = f", {', '.join(parts)}" if parts else ""
            self._status_prefix = f"Tag filter: {label} ({loaded_images.shown} images{suffix})"
            self._status_label.setText(self._status_prefix)
        else:
            # Filter cleared — reset mode tracking and return to folder view
            self._active_tag_mode = "AND"
            if self._current_folder:
                self._status_prefix = f"Folder: {os.path.basename(self._current_folder)}"
                self._status_label.setToolTip(self._current_folder)
                self._gallery.load_folder(self._current_folder)

    def _show_album_dialog(self):
        if self._album_dialog is None:
            self._album_dialog = QDialog(self)
            self._album_dialog.setWindowTitle("Albums")
            self._album_dialog.resize(260, 400)
            self._album_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            dlg_layout = QVBoxLayout(self._album_dialog)
            dlg_layout.setContentsMargins(0, 0, 0, 0)
            dlg_layout.addWidget(self._album_panel)
            geom = self._settings.value("album_dialog_geometry")
            if geom:
                self._album_dialog.restoreGeometry(geom)
            self._album_dialog.rejected.connect(
                lambda: self._settings.setValue(
                    "album_dialog_geometry", self._album_dialog.saveGeometry()
                )
            )
        self._album_dialog.show()
        self._album_dialog.raise_()
        self._album_dialog.activateWindow()

    def closeEvent(self, event):
        self._settings.setValue("splitter_state", self._splitter.saveState())
        self._settings.setValue("left_panel_visible", self._left_panel.isVisible())
        self._settings.setValue("right_panel_visible", self._right_panel.isVisible())
        if self._album_dialog is not None:
            self._settings.setValue("album_dialog_geometry", self._album_dialog.saveGeometry())
        super().closeEvent(event)

    def _on_album_selected(self, album_id: int):
        self._active_album_id = album_id
        self._active_tag_filter = []
        self._active_collection_id = None
        self._act_save_collection.setEnabled(False)
        rows = db.get_images_in_album(album_id)
        album = db.get_album(album_id)
        loaded_images = self._gallery.load_images(rows)
        parts = []
        if loaded_images.sfw_hidden > 0:
            parts.append(f"{loaded_images.sfw_hidden} hidden by SFW Mode")
        if loaded_images.missing > 0:
            parts.append(f"{loaded_images.missing} missing from disk")
        suffix = f", {', '.join(parts)}" if parts else ""
        self._status_prefix = f"Album: {album['name']} ({loaded_images.shown} images{suffix})"
        self._status_label.setText(self._status_prefix)

    def _on_sfw_toggle(self, checked: bool):
        self._sfw_mode = checked
        self._settings.setValue("sfw_mode", checked)
        excluded = ["rating:explicit", "rating:questionable"] if checked else []
        self._gallery.set_rating_filter(excluded)
        self._tag_panel.set_sfw_mode(checked)
        self._sfw_indicator.setVisible(checked)
        self._reload_current_view()

    def _reload_current_view(self):
        if self._active_collection_id is not None:
            self._on_collection_selected(self._active_collection_id)
        elif self._active_album_id is not None:
            self._on_album_selected(self._active_album_id)
        elif self._active_tag_filter:
            self._on_tag_filter(self._active_tag_filter, self._active_tag_mode)
        elif self._current_folder:
            self._gallery.load_folder(self._current_folder)

    def _on_collection_selected(self, filter_id: int):
        row = db.get_saved_filter(filter_id)
        if not row:
            return
        self._active_collection_id = filter_id
        self._active_tag_filter = []
        self._active_album_id = None
        self._act_save_collection.setEnabled(False)
        try:
            tags = json.loads(row["tags"])
        except (ValueError, TypeError):
            tags = []
        mode = row["mode"]
        rows = (db.get_images_by_tags_and(tags) if mode == "AND"
                else db.get_images_by_tags_or(tags))
        loaded = self._gallery.load_images(rows, show_folder_origin=True)
        parts = []
        if loaded.sfw_hidden > 0:
            parts.append(f"{loaded.sfw_hidden} hidden by SFW Mode")
        if loaded.missing > 0:
            parts.append(f"{loaded.missing} missing from disk")
        suffix = f", {', '.join(parts)}" if parts else ""
        tag_preview = ", ".join(tags[:2]) + ("…" if len(tags) > 2 else "")
        self._status_prefix = (
            f"Collection: {row['name']} [{mode}] {tag_preview} ({loaded.shown} images{suffix})"
        )
        self._status_label.setText(self._status_prefix)

    def _save_current_filter_as_collection(self):
        if not self._active_tag_filter:
            return
        default_name = " + ".join(self._active_tag_filter[:2])
        name, ok = QInputDialog.getText(
            self, "Save Collection", "Collection name:", text=default_name
        )
        if not ok or not name.strip():
            return
        try:
            db.create_saved_filter(name.strip(), self._active_tag_filter, self._active_tag_mode)
            self._album_panel.refresh()
        except Exception:
            QMessageBox.warning(
                self, "Save Failed",
                "A collection with that name already exists."
            )

    def _on_empty_gallery_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Open Folder…", self._open_folder)
        menu.addAction("Scan Folder into Library", self._scan_folder)
        menu.addSeparator()
        tags_menu = menu.addMenu("Tags")
        act_add = tags_menu.addAction("Add tag…")
        act_add.setEnabled(False)
        act_add.setToolTip("Select images first")
        menu.addAction("Albums…", self._show_album_dialog)
        menu.exec(pos)

    def _on_context_menu(self, image_ids: list[int], pos):
        menu = QMenu(self)
        menu.addAction("View", lambda: self._view_image(image_ids[0]))
        menu.addAction("Triage from here…", lambda: self._open_triage_from(image_ids[0]))
        menu.addAction("Reveal in Tree", lambda: self._open_location_in_tree(image_ids))
        menu.addSeparator()
        menu.addAction("Move to…", lambda: self._move_images(image_ids))
        menu.addAction("Copy to…", lambda: self._copy_images(image_ids))
        menu.addSeparator()
        tags_menu = menu.addMenu("Tags")
        tags_menu.addAction("Add tag…", lambda: self._add_tag_to_images(image_ids))
        menu.addAction("Albums…", self._show_album_dialog)
        menu.addSeparator()
        menu.addAction("Delete (Trash)", lambda: self._delete_images(image_ids, trash=True))
        menu.addSeparator()
        act_perm = QAction("Delete Permanently", self)
        act_perm.triggered.connect(lambda: self._delete_images(image_ids, trash=False))
        act_perm.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
        menu.addAction(act_perm)
        menu.exec(pos)

    def _add_tag_to_images(self, image_ids: list[int]):
        name, ok = QInputDialog.getText(self, "Add Tag", "Tag name:")
        if not ok:
            return
        name = " ".join(name.split())
        if not name:
            return
        for image_id in image_ids:
            db.add_tag_to_image(image_id, name)
        self._tag_panel.refresh()

    def _view_image(self, image_id: int):
        row = db.get_image(image_id)
        if not row:
            return
        nav = self._make_image_nav_list()
        idx = next((i for i, (iid, _) in enumerate(nav) if iid == image_id), 0)
        ImageViewer(image_id, row["path"], self, all_images=nav, current_index=idx).exec()

    def _open_triage_mode(self):
        nav = self._make_image_nav_list()
        if not nav:
            QMessageBox.information(
                self, "Triage Mode", "No images to triage in the current view."
            )
            return
        selected = self._gallery.get_selected_ids()
        idx = next(
            (i for i, (iid, _) in enumerate(nav) if iid == (selected[0] if selected else -1)),
            0
        )
        iid, path = nav[idx]
        viewer = TriageImageViewer(iid, path, self, all_images=nav, current_index=idx)
        viewer.image_trashed.connect(self._on_triage_image_trashed)
        viewer.exec()

    def _open_triage_from(self, image_id: int):
        nav = self._make_image_nav_list()
        if not nav:
            return
        idx = next((i for i, (iid, _) in enumerate(nav) if iid == image_id), 0)
        iid, path = nav[idx]
        viewer = TriageImageViewer(iid, path, self, all_images=nav, current_index=idx)
        viewer.image_trashed.connect(self._on_triage_image_trashed)
        viewer.exec()

    def _on_triage_image_trashed(self, image_id: int):
        self._gallery.remove_image(image_id)

    def _on_dnd_folder_drop(self, image_ids: list[int], folder_path: str):
        """Handle images dragged from gallery and dropped onto a folder in the tree."""
        if self._file_op_worker and self._file_op_worker.isRunning():
            QMessageBox.information(self, "Busy",
                "A file operation is already in progress. Please wait.")
            return
        # Filter out images already in the destination folder (same-folder no-op)
        rows = db.get_images_batch(image_ids)
        norm_dest = os.path.normpath(folder_path)
        image_ids = [iid for iid in image_ids
                     if iid in rows
                     if os.path.normpath(os.path.dirname(rows[iid]["path"])) != norm_dest]
        if not image_ids:
            return
        n = len(image_ids)
        msg = f"Move {n} image{'s' if n != 1 else ''} to:\n{folder_path}"
        if self._active_album_id is not None or self._active_collection_id is not None:
            msg += "\n\nThese images will remain in their album(s) after moving."
        reply = QMessageBox.question(
            self, "Move Images",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_file_op("move", image_ids, folder_path)

    def _on_images_added_to_album(self, count: int, album_name: str):
        self._status_label.setText(
            f"Added {count} image{'s' if count != 1 else ''} to \"{album_name}\"")

    def _move_images(self, image_ids: list[int]):
        dest = QFileDialog.getExistingDirectory(self, "Move to Folder")
        if not dest:
            return
        n = len(image_ids)
        reply = QMessageBox.warning(
            self, "Confirm Move",
            f"Move {n} image{'s' if n != 1 else ''} to:\n{dest}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_file_op("move", image_ids, dest)

    def _copy_images(self, image_ids: list[int]):
        dest = QFileDialog.getExistingDirectory(self, "Copy to Folder")
        if not dest:
            return
        n = len(image_ids)
        reply = QMessageBox.question(
            self, "Confirm Copy",
            f"Copy {n} image{'s' if n != 1 else ''} to:\n{dest}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_file_op("copy", image_ids, dest)

    def _start_file_op(self, op: str, image_ids: list[int], dest: str):
        self._set_counter_progress_visible(True)
        self._progress.setRange(0, len(image_ids))
        self._progress.setValue(0)
        self._progress_counter.setText(f"0 / {len(image_ids)}")
        self._file_op_worker = FileOpWorker(op, image_ids, dest, self)
        if op == "move":
            self._file_op_worker.item_done.connect(self._gallery.remove_image)
        self._file_op_worker.item_error.connect(
            lambda img_id, msg: self._gallery.mark_image_error(img_id)
        )
        self._file_op_worker.progress.connect(
            lambda cur, tot: (self._progress.setValue(cur),
                              self._progress_counter.setText(f"{cur} / {tot}"))
        )
        self._file_op_worker.finished_op.connect(
            lambda success, errors: self._on_file_op_finished(op, success, errors)
        )
        self._file_op_worker.start()

    def _on_file_op_finished(self, op: str, success: int, errors: list):
        self._set_counter_progress_visible(False)
        verb = "Moved" if op == "move" else "Copied"
        self._status_label.setText(
            f"{verb} {success} image(s)" + (f" — {len(errors)} failed" if errors else "")
        )
        if errors:
            QMessageBox.warning(self, f"{verb} Errors", "\n".join(errors))

    def _delete_images(self, image_ids: list[int], trash: bool):
        action = "trash" if trash else "permanently delete"
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to {action} {len(image_ids)} image(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        errors = []
        deleted = 0
        for image_id in image_ids:
            try:
                file_ops.delete_image(image_id, use_trash=trash)
                self._gallery.remove_image(image_id)
                deleted += 1
            except Exception as e:
                errors.append(str(e))
        self._status_label.setText(
            f"Deleted {deleted} image(s)" + (f" — {len(errors)} failed" if errors else "")
        )
        if errors:
            QMessageBox.warning(self, "Delete Errors", "\n".join(errors))

    # ------------------------------------------------------------------ WD14

    def _is_ai_busy(self) -> bool:
        if self._wd14_worker and self._wd14_worker.isRunning():
            return True
        if self._rating_sort_worker and self._rating_sort_worker.isRunning():
            return True
        return False

    def _run_wd14_tagging(self):
        image_ids = self._gallery.get_selected_ids()
        if not image_ids:
            QMessageBox.information(self, "No Selection", "Select images to tag first.")
            return

        if self._is_ai_busy():
            QMessageBox.information(self, "Busy", "Another AI task is already running.")
            return

        self._set_counter_progress_visible(True)
        self._progress.setRange(0, len(image_ids))
        self._progress.setValue(0)
        self._progress_counter.setText(f"0 / {len(image_ids)}")
        self._status_label.setText(f"Tagging {len(image_ids)} image(s) with WD14…")
        self._act_cancel_wd14.setEnabled(True)
        self._act_wd14.setEnabled(False)

        self._wd14_worker = WD14Worker(image_ids)
        self._wd14_worker.progress.connect(self._on_wd14_progress)
        self._wd14_worker.image_done.connect(self._on_wd14_done)
        self._wd14_worker.error.connect(self._on_wd14_error)
        self._wd14_worker.finished_all.connect(self._on_wd14_finished)
        self._wd14_worker.finished.connect(self._on_wd14_thread_finished)
        self._wd14_worker.start()

    def _cancel_wd14_tagging(self):
        if self._wd14_worker:
            self._wd14_worker.cancel()

    def _on_wd14_progress(self, current: int, total: int):
        self._progress.setValue(current)
        self._progress_counter.setText(f"{current} / {total}")

    def _on_wd14_done(self, image_id: int, filename: str, tags: list):
        self._status_label.setText(f"Tagged: {filename} (+{len(tags)} tags)")
        self._tag_refresh_timer.start()

    def _on_wd14_error(self, image_id: int, msg: str):
        self._status_label.setText(f"WD14 error on image {image_id}: {msg}")

    def _on_wd14_thread_finished(self):
        """Safety net: hides progress if finished_all never emitted (e.g. worker crash)."""
        self._act_cancel_wd14.setEnabled(False)
        self._act_wd14.setEnabled(True)
        if self._progress.isVisible():
            self._set_counter_progress_visible(False)

    def _on_wd14_finished(self, tagged: int, skipped: int, errors: int):
        self._set_counter_progress_visible(False)
        self._act_cancel_wd14.setEnabled(False)
        self._act_wd14.setEnabled(True)
        parts = [f"{tagged} tagged"]
        if skipped:
            parts.append(f"{skipped} skipped (already tagged)")
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        self._status_label.setText(f"WD14 done: {', '.join(parts)}.")
        self._tag_refresh_timer.start()

    # ------------------------------------------------------------------ Rating Sort

    def _run_rating_sort(self):
        if not self._current_folder:
            QMessageBox.information(self, "No Folder", "Open a folder first.")
            return

        if self._is_ai_busy():
            QMessageBox.information(self, "Busy", "Another AI task is already running.")
            return

        rows = db.get_images_with_ratings_in_folder(self._current_folder)
        sfw_count  = sum(1 for r in rows if r["rating"] in ("rating:general", "rating:sensitive"))
        nsfw_count = sum(1 for r in rows if r["rating"] in ("rating:explicit", "rating:questionable"))
        skipped    = len(rows) - sfw_count - nsfw_count

        reply = QMessageBox.information(
            self, "Sort into SFW/NSFW by Tags",
            f"Folder: {self._current_folder}\n\n"
            f"  • {sfw_count} image(s) → SFW  (general, sensitive)\n"
            f"  • {nsfw_count} image(s) → NSFW (explicit, questionable)\n"
            f"  • {skipped} image(s) skipped (no rating tag)\n\n"
            "Select destination folders next.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        sfw_folder = QFileDialog.getExistingDirectory(self, "Select SFW destination folder")
        if not sfw_folder:
            self._status_label.setText("Sort cancelled.")
            return
        nsfw_folder = QFileDialog.getExistingDirectory(self, "Select NSFW destination folder")
        if not nsfw_folder:
            self._status_label.setText("Sort cancelled.")
            return

        self._set_counter_progress_visible(True)
        self._progress.setRange(0, sfw_count + nsfw_count)
        self._progress.setValue(0)
        self._progress_counter.setText(f"0 / {sfw_count + nsfw_count}")
        self._status_label.setText(f"Sorting {sfw_count + nsfw_count} image(s)…")

        self._act_cancel_sort.setEnabled(True)
        self._act_sort.setEnabled(False)
        self._rating_sort_worker = RatingSortWorker(self._current_folder, sfw_folder, nsfw_folder)
        self._rating_sort_worker.progress.connect(self._on_sort_progress)
        self._rating_sort_worker.image_done.connect(self._on_sort_image_done)
        self._rating_sort_worker.error.connect(self._on_sort_error)
        self._rating_sort_worker.finished_all.connect(self._on_sort_finished)
        self._rating_sort_worker.finished.connect(self._on_sort_thread_finished)
        self._rating_sort_worker.start()

    def _cancel_rating_sort(self):
        if self._rating_sort_worker:
            self._rating_sort_worker.cancel()

    def _on_sort_progress(self, current: int, total: int):
        self._progress.setValue(current)
        self._progress_counter.setText(f"{current} / {total}")

    def _on_sort_image_done(self, image_id: int, dest: str):
        self._gallery.remove_image(image_id)

    def _on_sort_error(self, image_id: int, msg: str):
        self._status_label.setText(f"Sort error on image {image_id}: {msg}")

    def _on_sort_thread_finished(self):
        """Safety net: hides progress if finished_all never emitted (e.g. worker crash)."""
        self._act_cancel_sort.setEnabled(False)
        self._act_sort.setEnabled(True)
        if self._progress.isVisible():
            self._set_counter_progress_visible(False)

    def _on_sort_finished(self, sfw: int, nsfw: int, skipped: int):
        self._set_counter_progress_visible(False)
        self._act_cancel_sort.setEnabled(False)
        self._act_sort.setEnabled(True)
        self._status_label.setText(
            f"Sort complete: {sfw} → SFW, {nsfw} → NSFW, {skipped} skipped."
        )
        self._gallery.load_folder(self._current_folder)
        self._folder_tree.set_root(self._current_folder)
        self._tag_refresh_timer.start()
