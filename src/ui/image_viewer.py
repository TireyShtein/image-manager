import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QGraphicsView, QGraphicsScene)
from PyQt6.QtCore import Qt, QRectF, QUrl
from PyQt6.QtGui import QPixmap, QWheelEvent, QKeyEvent
import subprocess
import sys

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.wmv', '.flv', '.m4v'}


class ImageViewer(QDialog):
    def __init__(self, image_id: int, image_path: str, parent=None):
        super().__init__(parent)
        self.image_id = image_id
        self.image_path = image_path
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
        self._path_label = QLabel(self.image_path)
        self._path_label.setStyleSheet("color: gray; font-size: 11px;")
        bar.addWidget(self._path_label, 1)

        btn_fit = QPushButton("Fit")
        btn_fit.setFixedWidth(60)
        btn_fit.clicked.connect(self._fit)
        bar.addWidget(btn_fit)

        btn_100 = QPushButton("100%")
        btn_100.setFixedWidth(60)
        btn_100.clicked.connect(self._actual_size)
        bar.addWidget(btn_100)

        layout.addLayout(bar)

    def _load_image(self):
        pixmap = QPixmap(self.image_path)
        scene = QGraphicsScene()
        scene.addPixmap(pixmap)
        self._view.setScene(scene)
        self._view.setSceneRect(QRectF(pixmap.rect()))
        self._fit()

    def _fit(self):
        self._view.fitInView(self._view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _actual_size(self):
        self._view.resetTransform()


class ZoomableGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._zoom = 1.0

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom *= factor
        self._zoom = max(0.05, min(self._zoom, 50.0))
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.parent().close()
        else:
            super().keyPressEvent(event)
