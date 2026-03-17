import os
from PIL import Image
from src.core import database as db

SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}


def scan_folder(folder: str, progress_callback=None) -> int:
    """Scan a folder recursively, adding new images to the DB. Returns count added."""
    added = 0
    all_files = []
    for root, _, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS:
                all_files.append(os.path.join(root, f))

    for i, path in enumerate(all_files):
        if progress_callback:
            progress_callback(i, len(all_files))
        if not db.get_image_by_path(path):
            try:
                width, height = _get_dimensions(path)
                size = os.path.getsize(path)
                db.add_image(path, os.path.basename(path), width, height, size)
                added += 1
            except Exception:
                pass

    return added


def _get_dimensions(path: str):
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None, None
