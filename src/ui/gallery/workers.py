import os
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal, pyqtSlot
from src.core import thumbnail_cache, database as db


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
        name_path_pairs = []
        try:
            with os.scandir(self._folder) as entries:
                for entry in entries:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in self._media_exts:
                        name_path_pairs.append((entry.name.lower(), entry.path))
        except OSError:
            pass
        sorted_paths = [p for _, p in sorted(name_path_pairs)]
        rows, recovered = db.get_or_create_images_batch(sorted_paths)
        self._signals.rows_ready.emit(rows, self._token, recovered)


class _HoverCardSignals(QObject):
    ready = pyqtSignal(int, str, list)  # (token, size_str, tag_names)


class _HoverCardRunnable(QRunnable):
    def __init__(self, image_id: int, path: str, token: int, signals: _HoverCardSignals):
        super().__init__()
        self._image_id = image_id
        self._path = path
        self._token = token
        self._signals = signals
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        try:
            try:
                size_bytes = os.path.getsize(self._path)
                if size_bytes >= 1_048_576:
                    size_str = f"{size_bytes / 1_048_576:.1f} MB"
                elif size_bytes >= 1024:
                    size_str = f"{size_bytes / 1024:.0f} KB"
                else:
                    size_str = f"{size_bytes} B"
            except OSError:
                size_str = "—"
            tag_rows = db.get_tags_for_images([self._image_id])
            tag_names = [r[0] for r in tag_rows]
            self._signals.ready.emit(self._token, size_str, tag_names)
        except Exception:
            pass
