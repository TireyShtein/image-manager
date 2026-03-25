"""Batch WD14 tagger — pages 12-39 of the current gallery folder.

Calls wd14_tagger.classify() directly without a Qt event loop,
so it works in any terminal (no display/GUI required).

Usage:
    .venv/Scripts/python.exe batch_tag.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core import database as db
from src.core.image_scanner import SUPPORTED_EXTENSIONS
from src.core.thumbnail_cache import VIDEO_EXTENSIONS
from src.ai import wd14_tagger

# ── Config ────────────────────────────────────────────────────────────────────
PAGE_SIZE  = 200
START_PAGE = 12   # 1-indexed, as shown in the UI
END_PAGE   = 39   # 1-indexed, inclusive
# ──────────────────────────────────────────────────────────────────────────────


def main():
    db.init_db()

    # Resolve folder from QSettings (last folder opened in the app)
    # Import PyQt6 only for settings — no QApplication needed
    from PyQt6.QtCore import QSettings
    settings = QSettings("ImageManager", "ImageManager")
    folder = settings.value("last_folder", "")
    if not folder or not os.path.isdir(folder):
        print(f"[ERROR] No valid last_folder in QSettings: {folder!r}")
        print("        Open the Telegram folder in the app first, then run this script.")
        sys.exit(1)

    print(f"[INFO] Folder : {folder}")

    # Collect all media files sorted by filename (matches gallery sort)
    media_exts = SUPPORTED_EXTENSIONS | VIDEO_EXTENSIONS
    paths = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in media_exts:
                    paths.append(entry.path)
    except PermissionError:
        print(f"[ERROR] Permission denied: {folder}")
        sys.exit(1)

    paths.sort(key=lambda p: os.path.basename(p).lower())
    total_files = len(paths)
    print(f"[INFO] Total media files : {total_files}")

    # Slice pages 12-39  (1-indexed UI page N → 0-indexed slice (N-1)*PAGE_SIZE)
    start_idx   = (START_PAGE - 1) * PAGE_SIZE   # 2200
    end_idx     = END_PAGE * PAGE_SIZE             # 7800
    batch_paths = paths[start_idx:end_idx]

    if not batch_paths:
        print(f"[INFO] No files in page range {START_PAGE}–{END_PAGE}. Nothing to do.")
        sys.exit(0)

    actual_end = min(end_idx, total_files)
    print(f"[INFO] Pages {START_PAGE}-{END_PAGE}: "
          f"files {start_idx + 1}-{actual_end}  ({len(batch_paths)} files)")

    # Register images in DB and get IDs
    rows, _recovered = db.get_or_create_images_batch(batch_paths)
    image_ids = [r["id"] for r in rows]
    print(f"[INFO] Image IDs ready   : {len(image_ids)}")

    # Skip already-tagged images
    already_rated = db.get_image_ids_with_rating_tag(image_ids)
    image_map     = db.get_images_batch(image_ids)
    remaining     = [iid for iid in image_ids if iid not in already_rated]
    print(f"[INFO] Already tagged    : {len(already_rated)}  /  To tag: {len(remaining)}")
    print()

    if not remaining:
        print("[DONE] All images in this range are already tagged.")
        return

    tagged  = 0
    skipped = 0
    errors  = 0
    total   = len(remaining)

    for i, image_id in enumerate(remaining):
        row = image_map.get(image_id)
        if not row:
            skipped += 1
            continue

        # Progress bar (ASCII only — Windows console safe)
        pct = int((i + 1) / total * 100)
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        fname = os.path.basename(row["path"])[:40].encode("ascii", "replace").decode()
        print(f"\r  [{bar}] {pct:3d}%  {i + 1}/{total}  {fname:<40}",
              end="", flush=True)

        try:
            tags = wd14_tagger.classify(row["path"])
            db.add_tags_to_image_batch(image_id, [tag for tag, conf in tags])
            if tags:
                db.save_ai_result(image_id, "wd14", tags[0][0], tags[0][1])
            tagged += 1
        except Exception as e:
            errors += 1
            print(f"\n  [ERROR] id={image_id} {os.path.basename(row['path'])}: {e}")

    print(f"\n\n[DONE] Tagged: {tagged}  Skipped (no DB row): {skipped}  Errors: {errors}")


if __name__ == "__main__":
    main()
