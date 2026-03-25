import traceback
from PyQt6.QtCore import QThread, pyqtSignal
from src.core import database as db


class DuplicateScanWorker(QThread):
    phase_changed = pyqtSignal(str)   # status text for the status bar
    progress = pyqtSignal(int, int)   # (current, total) during hash phase
    scan_complete = pyqtSignal(list)  # list[list[dict]] — duplicate groups
    error = pyqtSignal(str)           # fatal error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # Phase 1: compute content_hash for any unhashed images
            unhashed = db.get_images_without_hash()
            total = len(unhashed)
            if total > 0:
                self.phase_changed.emit(f"Hashing {total} unprocessed image(s)…")
                for i, row in enumerate(unhashed):
                    if self._cancelled:
                        self.scan_complete.emit([])
                        return
                    self.progress.emit(i, total)
                    h = db.compute_content_hash(row["path"])
                    if h:
                        db.update_content_hash(row["id"], h)
                    # If h is None (file missing/unreadable), skip silently —
                    # row stays with content_hash='' and is excluded from query
                self.progress.emit(total, total)

            # Phase 2: find duplicate groups
            self.phase_changed.emit("Finding duplicates…")
            raw_groups = db.get_duplicate_groups()

            # Convert sqlite3.Row → dict before crossing the thread boundary
            # (sqlite3.Row holds a reference to the originating connection cursor)
            groups = [[dict(r) for r in grp] for grp in raw_groups]
            self.scan_complete.emit(groups)
        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            db.close_connection()
