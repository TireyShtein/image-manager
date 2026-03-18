import os
from PyQt6.QtWidgets import (QMainWindow, QPushButton, QWidget, QHBoxLayout, QVBoxLayout,
                              QSplitter, QStatusBar, QProgressBar, QLabel,
                              QFileDialog, QMessageBox, QInputDialog, QMenu,
                              QApplication)
from PyQt6.QtCore import Qt, QThread, QSettings, QTimer
from PyQt6.QtGui import QAction
from src.ui.folder_tree import FolderTree
from src.ui.gallery_view import GalleryView
from src.ui.image_viewer import ImageViewer, VIDEO_EXTENSIONS
from src.ui.tag_panel import TagPanel
from src.ui.album_panel import AlbumPanel
from src.core import database as db, image_scanner, file_ops
from src.ai.classifier_worker import ClassifierWorker
from src.ai.wd14_worker import WD14Worker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Manager")
        self.resize(1280, 800)
        self._current_folder: str | None = None
        self._classifier_worker: ClassifierWorker | None = None
        self._wd14_worker: WD14Worker | None = None
        self._settings = QSettings("ImageManager", "ImageManager")
        self._status_prefix = "Ready"
        _db_is_new = not db.db_exists()
        db.init_db()
        if _db_is_new:
            print("[DB] Created new database at:", db.DB_PATH)
        else:
            print("[DB] Opened existing database at:", db.DB_PATH)
        self._build_ui()
        self._build_menu()
        self._build_statusbar()

        # Debounced tag-panel refresh for AI signal handlers
        self._tag_refresh_timer = QTimer(self)
        self._tag_refresh_timer.setSingleShot(True)
        self._tag_refresh_timer.setInterval(500)
        self._tag_refresh_timer.timeout.connect(self._tag_panel.refresh)

        self._restore_last_folder()
        self._tag_panel.refresh()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        # Left: go-up button + folder tree
        left = QWidget()
        left.setMinimumWidth(180)
        left.setMaximumWidth(300)
        left_layout = QVBoxLayout(left)
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
        left_layout.addWidget(self._folder_tree)
        splitter.addWidget(left)

        # Centre: gallery
        self._gallery = GalleryView()
        self._gallery.image_double_clicked.connect(self._on_image_double_clicked)
        self._gallery.context_menu_requested.connect(self._on_context_menu)
        self._gallery.selection_changed.connect(self._on_selection_changed)
        self._gallery.thumbnails_loading.connect(self._on_thumbnails_loading)
        self._gallery.thumbnails_ready.connect(self._on_thumbnails_ready)
        self._gallery.empty_context_menu_requested.connect(self._on_empty_gallery_context_menu)
        splitter.addWidget(self._gallery)

        # Right: tag + album panels stacked
        right = QSplitter(Qt.Orientation.Vertical)
        right.setMinimumWidth(180)
        right.setMaximumWidth(280)

        self._tag_panel = TagPanel()
        self._tag_panel.tag_filter_changed.connect(self._on_tag_filter)
        right.addWidget(self._tag_panel)

        self._album_panel = AlbumPanel()
        self._album_panel.album_selected.connect(self._on_album_selected)
        right.addWidget(self._album_panel)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

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

        ai_menu = mb.addMenu("AI")
        act_classify = QAction("Classify Selected Images…", self)
        act_classify.setShortcut("Ctrl+R")
        act_classify.triggered.connect(self._run_classification)
        ai_menu.addAction(act_classify)

        act_cancel = QAction("Cancel Classification", self)
        act_cancel.triggered.connect(self._cancel_classification)
        ai_menu.addAction(act_cancel)

        ai_menu.addSeparator()
        act_wd14 = QAction("Tag with WD14…", self)
        act_wd14.setShortcut("Ctrl+T")
        act_wd14.triggered.connect(self._run_wd14_tagging)
        ai_menu.addAction(act_wd14)

        act_cancel_wd14 = QAction("Cancel WD14 Tagging", self)
        act_cancel_wd14.triggered.connect(self._cancel_wd14_tagging)
        ai_menu.addAction(act_cancel_wd14)

    def _build_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_label = QLabel("Ready")
        self._statusbar.addWidget(self._status_label, 1)

        self._selected_label = QLabel("")
        self._statusbar.addWidget(self._selected_label)

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
            self._status_prefix = f"Folder: {folder}"
            self._gallery.load_folder(folder)
            self._status_label.setText(self._status_prefix)
            self._update_go_up_button()

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            self._current_folder = folder
            self._status_prefix = f"Folder: {folder}"
            self._gallery.load_folder(folder)
            self._folder_tree.set_root(folder)
            self._status_label.setText(self._status_prefix)
            self._settings.setValue("last_folder", folder)
            self._tag_panel.clear_search()
            self._update_go_up_button()

    def _go_up_folder(self):
        if not self._current_folder:
            return
        parent = os.path.dirname(self._current_folder)
        if not parent or parent == self._current_folder:
            return
        self._current_folder = parent
        self._status_prefix = f"Folder: {parent}"
        self._folder_tree.set_root(parent)
        self._gallery.load_folder(parent)
        self._status_label.setText(self._status_prefix)
        self._settings.setValue("last_folder", parent)
        self._tag_panel.clear_search()
        self._update_go_up_button()

    def _update_go_up_button(self):
        if self._current_folder:
            parent = os.path.dirname(self._current_folder)
            self._btn_go_up.setEnabled(bool(parent) and parent != self._current_folder)
        else:
            self._btn_go_up.setEnabled(False)

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
        self._status_label.setText(f"Scanning {folder}…")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)

        def progress_cb(current, total):
            if total:
                self._progress.setRange(0, total)
                self._progress.setValue(current)
            QApplication.processEvents()

        added = image_scanner.scan_folder(folder, progress_cb)
        self._progress.setVisible(False)
        self._status_label.setText(f"Scanned: {added} new images added from {folder}")
        if self._current_folder == folder:
            self._gallery.load_folder(folder)

    def _on_folder_selected(self, folder: str):
        self._current_folder = folder
        self._status_prefix = f"Folder: {folder}"
        self._gallery.load_folder(folder)
        self._status_label.setText(self._status_prefix)
        self._settings.setValue("last_folder", folder)
        self._tag_panel.clear_search()
        self._update_go_up_button()

    def _on_tree_files_selected(self, paths: list[str]):
        self._gallery.load_paths(paths)
        self._status_label.setText(f"{len(paths)} file(s) selected in tree")
        self._tag_panel.clear_search()

    def _on_thumbnails_loading(self, loaded: int, total: int):
        self._status_label.setText(f"{self._status_prefix} — Loading {loaded}/{total}…")

    def _on_thumbnails_ready(self, count: int):
        if self._status_prefix.startswith("Folder:"):
            self._status_label.setText(f"{self._status_prefix} ({count} images)")
        else:
            self._status_label.setText(self._status_prefix)

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
        self._selected_label.setText(f"{count} selected")

    def _on_tag_filter(self, tag_name: str):
        if tag_name:
            rows = db.get_images_by_tag(tag_name)
            shown = self._gallery.load_images(rows, empty_text=f"No images with tag '{tag_name}' found on disk")
            missing = len(rows) - shown
            suffix = f", {missing} missing from disk" if missing else ""
            self._status_prefix = f"Tag filter: {tag_name} ({shown} images{suffix})"
            self._status_label.setText(self._status_prefix)
        elif self._current_folder:
            self._status_prefix = f"Folder: {self._current_folder}"
            self._gallery.load_folder(self._current_folder)

    def _on_album_selected(self, album_id: int):
        rows = db.get_images_in_album(album_id)
        album = db.get_album(album_id)
        shown = self._gallery.load_images(rows, empty_text=f"No images in album '{album['name']}' found on disk")
        missing = len(rows) - shown
        suffix = f", {missing} missing from disk" if missing else ""
        self._status_prefix = f"Album: {album['name']} ({shown} images{suffix})"
        self._status_label.setText(self._status_prefix)

    def _on_empty_gallery_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Open Folder…", self._open_folder)
        menu.addAction("Scan Folder into Library", self._scan_folder)
        menu.exec(pos)

    def _on_context_menu(self, image_ids: list[int], pos):
        menu = QMenu(self)
        menu.addAction("View", lambda: self._view_image(image_ids[0]))
        menu.addSeparator()
        menu.addAction("Move to…", lambda: self._move_images(image_ids))
        menu.addAction("Copy to…", lambda: self._copy_images(image_ids))
        menu.addSeparator()
        tags_menu = menu.addMenu("Tags")
        tags_menu.addAction("Add tag…", lambda: self._add_tag_to_images(image_ids))
        menu.addSeparator()
        menu.addAction("Delete (Trash)", lambda: self._delete_images(image_ids, trash=True))
        menu.addAction("Delete Permanently", lambda: self._delete_images(image_ids, trash=False))
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

    def _move_images(self, image_ids: list[int]):
        dest = QFileDialog.getExistingDirectory(self, "Move to Folder")
        if not dest:
            return
        errors = []
        for image_id in image_ids:
            try:
                file_ops.move_image(image_id, dest)
                self._gallery.remove_image(image_id)
            except Exception as e:
                errors.append(str(e))
        if errors:
            QMessageBox.warning(self, "Move Errors", "\n".join(errors))

    def _copy_images(self, image_ids: list[int]):
        dest = QFileDialog.getExistingDirectory(self, "Copy to Folder")
        if not dest:
            return
        errors = []
        for image_id in image_ids:
            try:
                file_ops.copy_image(image_id, dest)
            except Exception as e:
                errors.append(str(e))
        if errors:
            QMessageBox.warning(self, "Copy Errors", "\n".join(errors))

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
        for image_id in image_ids:
            try:
                file_ops.delete_image(image_id, use_trash=trash)
                self._gallery.remove_image(image_id)
            except Exception as e:
                errors.append(str(e))
        if errors:
            QMessageBox.warning(self, "Delete Errors", "\n".join(errors))

    # ------------------------------------------------------------------ AI

    def _run_classification(self):
        image_ids = self._gallery.get_selected_ids()
        if not image_ids:
            QMessageBox.information(self, "No Selection", "Select images to classify first.")
            return

        if self._classifier_worker and self._classifier_worker.isRunning():
            QMessageBox.information(self, "Busy", "Classification already running.")
            return

        # Explain what is about to happen before asking for folders
        reply = QMessageBox.information(
            self, "AI Classification",
            f"About to classify {len(image_ids)} image(s).\n\n"
            "Step 1 — NSFW detection: images are sorted into a Safe (SFW) or "
            "Not-Safe-For-Work (NSFW) folder.\n"
            "Step 2 — Content tagging: top-3 ImageNet labels are added as tags.\n\n"
            "You will now be asked to choose:\n"
            "  • A destination folder for SFW images\n"
            "  • A destination folder for NSFW images",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        sfw_folder = QFileDialog.getExistingDirectory(self, "Select SFW destination folder")
        if not sfw_folder:
            return
        nsfw_folder = QFileDialog.getExistingDirectory(self, "Select NSFW destination folder")
        if not nsfw_folder:
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, len(image_ids))
        self._progress.setValue(0)
        self._status_label.setText(f"Classifying {len(image_ids)} images…")

        self._classifier_worker = ClassifierWorker(image_ids, sfw_folder, nsfw_folder)
        self._classifier_worker.progress.connect(self._on_classify_progress)
        self._classifier_worker.image_done.connect(self._on_classify_result)
        self._classifier_worker.error.connect(self._on_classify_error)
        self._classifier_worker.finished_all.connect(self._on_classify_finished)
        self._classifier_worker.start()

    def _cancel_classification(self):
        if self._classifier_worker:
            self._classifier_worker.cancel()

    def _on_classify_progress(self, current: int, total: int):
        self._progress.setValue(current)

    def _on_classify_result(self, image_id: int, stage: str, label: str, confidence: float):
        if stage == "nsfw":
            self._gallery.remove_image(image_id)
        self._tag_refresh_timer.start()

    def _on_classify_error(self, image_id: int, msg: str):
        self._status_label.setText(f"Error on image {image_id}: {msg}")

    def _on_classify_finished(self):
        self._progress.setVisible(False)
        self._status_label.setText("Classification complete.")
        self._album_panel.refresh()
        self._tag_refresh_timer.start()

    # ------------------------------------------------------------------ WD14

    def _run_wd14_tagging(self):
        image_ids = self._gallery.get_selected_ids()
        if not image_ids:
            QMessageBox.information(self, "No Selection", "Select images to tag first.")
            return

        if self._wd14_worker and self._wd14_worker.isRunning():
            QMessageBox.information(self, "Busy", "WD14 tagging already running.")
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, len(image_ids))
        self._progress.setValue(0)
        self._status_label.setText(f"Tagging {len(image_ids)} image(s) with WD14…")

        self._wd14_worker = WD14Worker(image_ids)
        self._wd14_worker.progress.connect(self._on_wd14_progress)
        self._wd14_worker.image_done.connect(self._on_wd14_done)
        self._wd14_worker.error.connect(self._on_wd14_error)
        self._wd14_worker.finished_all.connect(self._on_wd14_finished)
        self._wd14_worker.start()

    def _cancel_wd14_tagging(self):
        if self._wd14_worker:
            self._wd14_worker.cancel()

    def _on_wd14_progress(self, current: int, total: int):
        self._progress.setValue(current)

    def _on_wd14_done(self, image_id: int, tags: list):
        self._tag_refresh_timer.start()

    def _on_wd14_error(self, image_id: int, msg: str):
        self._status_label.setText(f"WD14 error on image {image_id}: {msg}")

    def _on_wd14_finished(self):
        self._progress.setVisible(False)
        self._status_label.setText("WD14 tagging complete.")
        self._tag_refresh_timer.start()
