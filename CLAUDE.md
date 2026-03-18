# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Install dependencies (use the project venv)
.venv\Scripts\pip install -r requirements.txt

# Run
.venv\Scripts\python main.py
```

> **Windows DLL gotcha:** `import torch` must appear before any PyQt6 import in `main.py`. PyQt6 modifies the Windows DLL search path at import time, which prevents PyTorch's `c10.dll` from loading. This is already handled in `main.py:2`.

There are no tests or linting configured yet.

## Architecture

The app is split into three layers that communicate via PyQt6 signals:

```
UI Layer (src/ui/)
  MainWindow          — layout host, signal wiring, file/AI action handlers
  FolderTree          — QFileSystemModel tree; emits folder_selected / files_selected
  GalleryView         — virtual-scroll QListView; async thumbnail loading via QThreadPool
  TagPanel / AlbumPanel — right sidebar; read/write DB directly, emit filter signals
  ImageViewer         — modal QDialog for full-size view; opens videos via os.startfile()

Core Layer (src/core/)
  database.py         — all SQLite access (functional API, no ORM)
  thumbnail_cache.py  — 256px JPEG cache in %APPDATA%/ImageManager/thumbs/, keyed by MD5(path)
  image_scanner.py    — recursive scan, populates DB
  file_ops.py         — move/copy/delete with DB path sync and trash support

AI Layer (src/ai/)
  ClassifierWorker    — QThread orchestrator; emits progress/image_done/error signals
  nsfw_detector.py    — Stage 1: falconsai/nsfw_image_detection → moves to SFW/ or NSFW/
  content_classifier.py — Stage 2: google/vit-base-patch16-224 → auto-tags top-3 ImageNet labels
  WD14Worker          — QThread for standalone WD14 tagging; emits progress/image_done/error signals
  wd14_tagger.py      — SmilingWolf/wd-swinv2-tagger-v3 via wdtagger; tags general/character/rating
```

### Threading model
- Thumbnail generation: `QThreadPool` (global) + `ThumbnailLoader(QRunnable)` per image
- AI classification: `ClassifierWorker(QThread)` — one worker at a time, cancellable
- WD14 tagging: `WD14Worker(QThread)` — one worker at a time, cancellable
- All cross-thread communication uses `pyqtSignal`

### Database
SQLite with WAL journal mode and foreign keys enabled. Tables: `images`, `tags`, `image_tags`, `albums`, `album_images`, `ai_results`. All access goes through `src/core/database.py`. Connection opened per-call via `get_connection()` (context manager, Row factory).

### Gallery loading
`GalleryView.load_folder(folder)` reads the filesystem directly (non-recursive) and auto-registers new files in the DB. This means no prior scan is needed to browse a folder. `load_paths(paths)` does the same for a list of individual files (used when files are selected directly in the tree).

### Thumbnail auto-sizing
`GalleryView` dynamically resizes thumbnails based on image count via `_compute_thumb_size(count)` and `_SIZE_TIERS` (≤4→300px … 500+→70px). `GalleryModel` stores both `source_pix` (full 256px from cache) and `display_pix` (scaled to current display size) so re-scaling is in-memory only — no disk re-read.

### Folder tree scoping
`FolderTree.set_root(path)` restricts the tree's visible root to a single folder (`setRootIndex`). Called by `MainWindow._open_folder()` and `_restore_last_folder()`. `navigate_to(path)` scrolls/selects without changing the root.

### Persistent last folder
`MainWindow` uses `QSettings("ImageManager", "ImageManager")` (Windows registry) to save and restore `last_folder`. On launch, `_restore_last_folder()` reloads the tree and gallery if the stored path still exists on disk.

### Tag panel
`TagPanel` has two separate `QListWidget`s:
- **All Tags** — shows every tag in the DB with image counts via `db.get_all_tags_with_counts()`. Filtered live by the search bar via `db.search_tags_with_counts(query)`. Clicking a tag emits `tag_filter_changed` to filter the gallery.
- **Selected Image Tags** — shows tags for the first selected image. Filtered by the same search bar (client-side). Supports add/remove for all selected images.

The search bar has `QCompleter` autocomplete sourced from `wd14_tagger.get_all_tags()`, which reads `wdtagger/assets/selected_tags.csv` at runtime (no model load needed). Stale `image_tags` rows (images deleted/moved outside the app) are surfaced by the count query joining only valid `image_tags` rows.

### Media type definitions
Image extensions live in `src/core/image_scanner.py:SUPPORTED_EXTENSIONS`. Video extensions live in `src/core/thumbnail_cache.py:VIDEO_EXTENSIONS`. Both sets are combined in `folder_tree.py` and `gallery_view.py`.

### AI models
Models are downloaded from HuggingFace on first use and cached in `~/.cache/huggingface/`. All AI actions are manual-trigger only via the AI menu.

- **Classify Selected Images (Ctrl+R):** Stage 1 NSFW detection (~150 MB Falconsai) sorts images into SFW/NSFW folders; Stage 2 content tagging (~300 MB ViT) adds top-3 ImageNet labels as tags.
- **Tag with WD14… (Ctrl+T):** Tags images in-place using `SmilingWolf/wd-swinv2-tagger-v3` via the `wdtagger` package (~400 MB). Outputs general content tags, character tags, and a `rating:` tag (general/sensitive/questionable/explicit). Thresholds: general=0.35, character=0.9.
