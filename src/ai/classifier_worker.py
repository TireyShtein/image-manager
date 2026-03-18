from PyQt6.QtCore import QThread, pyqtSignal
from src.core import database as db
from src.core import file_ops
from src.ai import nsfw_detector, content_classifier


class ClassifierWorker(QThread):
    progress = pyqtSignal(int, int)           # (current, total)
    image_done = pyqtSignal(int, str, str, float)  # (image_id, stage, label, confidence)
    error = pyqtSignal(int, str)              # (image_id, error_message)
    finished_all = pyqtSignal()

    def __init__(self, image_ids: list[int], sfw_folder: str, nsfw_folder: str,
                 run_stage2: bool = True, parent=None):
        super().__init__(parent)
        self.image_ids = image_ids
        self.sfw_folder = sfw_folder
        self.nsfw_folder = nsfw_folder
        self.run_stage2 = run_stage2
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
            path = row["path"]
            try:
                # Stage 1: NSFW detection
                label, confidence = nsfw_detector.classify(path)
                db.save_ai_result(image_id, "nsfw", label, confidence)
                dest_folder = self.nsfw_folder if label == "nsfw" else self.sfw_folder
                new_path = file_ops.move_image(image_id, dest_folder)
                self.image_done.emit(image_id, "nsfw", label, confidence)

                # Stage 2: Content classification (on updated path)
                if self.run_stage2:
                    results = content_classifier.classify(new_path)
                    db.add_tags_to_image_batch(image_id, [tag_label for tag_label, _ in results])
                    for tag_label, tag_conf in results:
                        db.save_ai_result(image_id, "content", tag_label, tag_conf)
                        self.image_done.emit(image_id, "content", tag_label, tag_conf)

            except Exception as e:
                self.error.emit(image_id, str(e))

        self.progress.emit(total, total)
        self.finished_all.emit()
