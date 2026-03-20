import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QGraphicsView, QGraphicsScene)
from PyQt6.QtCore import Qt, QRectF, QRunnable, QThreadPool, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap, QImage, QWheelEvent, QKeyEvent
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

    def _load_image(self):
        # Show a loading placeholder while decode runs on a background thread
        self._set_nav_enabled(False)
        loading_scene = QGraphicsScene()
        loading_scene.addText("Loading…")
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
