from PyQt6.QtCore import QThread, pyqtSignal
from src.core import database as db, image_scanner, file_ops


class ScanWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_scan = pyqtSignal(int)  # number of images added

    def __init__(self, folder: str, parent=None):
        super().__init__(parent)
        self._folder = folder

    def run(self):
        try:
            def cb(current, total):
                self.progress.emit(current, total)
            added = image_scanner.scan_folder(self._folder, cb)
            self.finished_scan.emit(added)
        finally:
            db.close_connection()


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
        try:
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
        finally:
            db.close_connection()
