from PyQt6.QtCore import QThread, pyqtSignal
from src.core import database as db, file_ops


class RatingSortWorker(QThread):
    NSFW_RATINGS = {"rating:explicit", "rating:questionable"}
    SFW_RATINGS  = {"rating:general", "rating:sensitive"}

    progress     = pyqtSignal(int, int)       # (current, total)
    image_done   = pyqtSignal(int, str)        # (image_id, dest_path)
    error        = pyqtSignal(int, str)        # (image_id, message)
    finished_all = pyqtSignal(int, int, int)   # (sfw_count, nsfw_count, skipped_count)

    def __init__(self, folder: str, sfw_folder: str, nsfw_folder: str, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.sfw_folder = sfw_folder
        self.nsfw_folder = nsfw_folder
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        rows = db.get_images_with_ratings_in_folder(self.folder)
        total = len(rows)
        sfw_count = nsfw_count = skipped_count = 0

        for i, row in enumerate(rows):
            if self._cancelled:
                break
            self.progress.emit(i, total)
            image_id = row["id"]
            rating = row["rating"]

            if rating in self.NSFW_RATINGS:
                dest_folder = self.nsfw_folder
            elif rating in self.SFW_RATINGS:
                dest_folder = self.sfw_folder
            else:
                skipped_count += 1
                continue

            try:
                dest = file_ops.move_image(image_id, dest_folder)
                if dest_folder == self.sfw_folder:
                    sfw_count += 1
                else:
                    nsfw_count += 1
                self.image_done.emit(image_id, dest)
            except Exception as e:
                self.error.emit(image_id, str(e))

        self.progress.emit(total, total)
        self.finished_all.emit(sfw_count, nsfw_count, skipped_count)
