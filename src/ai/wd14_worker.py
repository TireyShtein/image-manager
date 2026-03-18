from PyQt6.QtCore import QThread, pyqtSignal
from src.core import database as db
from src.ai import wd14_tagger


class WD14Worker(QThread):
    progress = pyqtSignal(int, int)       # (current, total)
    image_done = pyqtSignal(int, list)    # (image_id, [(tag, conf), ...])
    error = pyqtSignal(int, str)          # (image_id, message)
    finished_all = pyqtSignal()

    def __init__(self, image_ids: list[int], parent=None):
        super().__init__(parent)
        self.image_ids = image_ids
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.image_ids)
        image_map = db.get_images_batch(self.image_ids)
        for i, image_id in enumerate(self.image_ids):
            if self._cancelled:
                break
            self.progress.emit(i, total)
            row = image_map.get(image_id)
            if not row:
                continue
            try:
                tags = wd14_tagger.classify(row["path"])
                db.add_tags_to_image_batch(image_id, [tag for tag, conf in tags])
                if tags:
                    db.save_ai_result(image_id, "wd14", tags[0][0], tags[0][1])
                self.image_done.emit(image_id, tags)
            except Exception as e:
                self.error.emit(image_id, str(e))
        self.progress.emit(total, total)
        self.finished_all.emit()
