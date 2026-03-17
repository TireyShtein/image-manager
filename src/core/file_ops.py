import os
import shutil
import send2trash
from src.core import database as db
from src.core.thumbnail_cache import delete_thumbnail


def move_image(image_id: int, dest_folder: str) -> str:
    row = db.get_image(image_id)
    if not row:
        raise ValueError(f"Image {image_id} not found in database")
    src = row["path"]
    os.makedirs(dest_folder, exist_ok=True)
    dest = _unique_path(dest_folder, row["filename"])
    shutil.move(src, dest)
    db.update_image_path(image_id, dest)
    return dest


def copy_image(image_id: int, dest_folder: str) -> str:
    row = db.get_image(image_id)
    if not row:
        raise ValueError(f"Image {image_id} not found in database")
    src = row["path"]
    os.makedirs(dest_folder, exist_ok=True)
    dest = _unique_path(dest_folder, row["filename"])
    shutil.copy2(src, dest)
    # Register the copy as a new image in the DB
    db.add_image(dest, os.path.basename(dest), row["width"], row["height"], row["file_size"])
    return dest


def delete_image(image_id: int, use_trash: bool = True):
    row = db.get_image(image_id)
    if not row:
        raise ValueError(f"Image {image_id} not found in database")
    path = row["path"]
    if os.path.exists(path):
        if use_trash:
            send2trash.send2trash(path)
        else:
            os.remove(path)
    delete_thumbnail(path)
    db.delete_image(image_id)


def _unique_path(folder: str, filename: str) -> str:
    dest = os.path.join(folder, filename)
    if not os.path.exists(dest):
        return dest
    name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(folder, f"{name}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1
