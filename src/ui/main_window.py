import os
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QSplitter, QStatusBar, QProgressBar, QLabel,
                              QFileDialog, QMessageBox, QInputDialog, QMenu,
                              QToolBar)
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QAction
from src.ui.folder_tree import FolderTree
from src.ui.gallery_view import GalleryView
from src.ui.image_viewer import ImageViewer
from src.ui.tag_panel import TagPanel
from src.ui.album_panel import AlbumPanel
from src.core import database as db, image_scanner, file_ops
from src.ai.classifier_worker import ClassifierWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Manager")
        self.resize(1280, 800)
        self._current_folder: str | None = None
        self._classifier_worker: ClassifierWorker | None = None
        db.init_db()
        self._build_ui()
        self._build_menu()
        self._build_statusbar()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        # Left: folder tree
        self._folder_tree = FolderTree()
        self._folder_tree.setMinimumWidth(180)
        self._folder_tree.setMaximumWidth(300)
        self._folder_tree.folder_selected.connect(self._on_folder_selected)
        splitter.addWidget(self._folder_tree)

        # Centre: gallery
        self._gallery = GalleryView()
        self._gallery.image_double_clicked.connect(self._on_image_double_clicked)
        self._gallery.context_menu_requested.connect(self._on_context_menu)
        self._gallery.selectionModel().selectionChanged.connect(self._on_selection_changed)
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

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            self._current_folder = folder
            self._gallery.load_folder(folder)
            self._folder_tree.navigate_to(folder)
            self._status_label.setText(f"Folder: {folder}")

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

        added = image_scanner.scan_folder(folder, progress_cb)
        self._progress.setVisible(False)
        self._status_label.setText(f"Scanned: {added} new images added from {folder}")
        if self._current_folder == folder:
            self._gallery.load_folder(folder)

    def _on_folder_selected(self, folder: str):
        self._current_folder = folder
        self._gallery.load_folder(folder)
        self._status_label.setText(f"Folder: {folder}")

    def _on_image_double_clicked(self, image_id: int):
        row = db.get_image(image_id)
        if row:
            viewer = ImageViewer(image_id, row["path"], self)
            viewer.exec()

    def _on_selection_changed(self, selected, deselected):
        ids = self._gallery.get_selected_ids()
        self._tag_panel.set_selected_images(ids)
        self._album_panel.set_selected_images(ids)
        count = len(ids)
        self._selected_label.setText(f"{count} selected" if count else "")

    def _on_tag_filter(self, tag_name: str):
        if tag_name:
            rows = db.get_images_by_tag(tag_name)
            self._gallery.load_images(rows)
            self._status_label.setText(f"Tag filter: {tag_name} ({len(rows)} images)")
        elif self._current_folder:
            self._gallery.load_folder(self._current_folder)

    def _on_album_selected(self, album_id: int):
        rows = db.get_images_in_album(album_id)
        self._gallery.load_images(rows)
        album = db.get_album(album_id)
        self._status_label.setText(f"Album: {album['name']} ({len(rows)} images)")

    def _on_context_menu(self, image_ids: list[int], pos):
        menu = QMenu(self)
        menu.addAction("View", lambda: self._view_image(image_ids[0]))
        menu.addSeparator()
        menu.addAction("Move to…", lambda: self._move_images(image_ids))
        menu.addAction("Copy to…", lambda: self._copy_images(image_ids))
        menu.addSeparator()
        menu.addAction("Delete (Trash)", lambda: self._delete_images(image_ids, trash=True))
        menu.addAction("Delete Permanently", lambda: self._delete_images(image_ids, trash=False))
        menu.exec(pos)

    def _view_image(self, image_id: int):
        row = db.get_image(image_id)
        if row:
            ImageViewer(image_id, row["path"], self).exec()

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

        # Ask for SFW/NSFW destination folders
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
        self._tag_panel.refresh()

    def _on_classify_error(self, image_id: int, msg: str):
        self._status_label.setText(f"Error on image {image_id}: {msg}")

    def _on_classify_finished(self):
        self._progress.setVisible(False)
        self._status_label.setText("Classification complete.")
        self._album_panel.refresh()
        self._tag_panel.refresh()
