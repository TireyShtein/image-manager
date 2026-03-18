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
`GalleryView` dynamically resizes thumbnails based on image count via `_compute_thumb_size(count)` and `_SIZE_TIERS` (≤4→300px, ≤12→240px, ≤30→180px, ≤80→140px, ≤200→110px, ≤500→85px, ≤700→78px, else→70px). `GalleryModel` stores both `source_pix` (full 256px from cache) and `display_pix` (scaled to current display size) so re-scaling is in-memory only — no disk re-read.

Grid cell height is `thumb_px + label_px` where `label_px = max(20, thumb_px // 5)` — this scales the filename label area with thumbnail size. Filenames are hidden (DisplayRole returns `None`) below 140px to avoid overlap; tooltip still shows full path. Grid cell width is `thumb_px + 16` (8px per side). Size tier is computed from `len(rows)` and applied to the model *before* `set_images()` fires `endResetModel()`, preventing a size-pop on folder change. Inter-item spacing is 8px.

### Gallery selection signal
`GalleryView` emits `selection_changed(list[int])` — a list of selected image IDs — whenever the selection changes. This is wired internally via `selectionModel().selectionChanged` → `_on_selection_changed` → `selection_changed.emit(...)`. `MainWindow` connects to `self._gallery.selection_changed` rather than reaching into `selectionModel()` directly, keeping the coupling at the domain level (IDs, not Qt model internals).

### Gallery empty state
`GalleryView` tracks a `_loading` flag. It is set `True` before `set_images()` when rows exist, and cleared to `False` in `_on_all_loaded()` (connected to `GalleryModel._signals.all_loaded`). The `paintEvent` empty-state overlay ("No media in this folder") is suppressed while `_loading` is `True`, preventing a false flash before thumbnails arrive. A genuinely empty folder (`len(rows) == 0`) never sets `_loading`, so the overlay appears immediately.

### Folder tree scoping
`FolderTree.set_root(path)` restricts the tree's visible root to a single folder (`setRootIndex`). Called by `MainWindow._open_folder()` and `_restore_last_folder()`. `navigate_to(path)` scrolls/selects without changing the root.

### Persistent last folder
`MainWindow` uses `QSettings("ImageManager", "ImageManager")` (Windows registry) to save and restore `last_folder`. On launch, `_restore_last_folder()` reloads the tree and gallery if the stored path still exists on disk.

### Tag panel
`TagPanel` has two separate `QListWidget`s:
- **All Tags** — shows every tag in the DB with image counts via `db.get_all_tags_with_counts()`. Filtered live by the search bar via `db.search_tags_with_counts(query)`. Clicking a tag emits `tag_filter_changed` to filter the gallery. A "Clear filter" ghost button appears when a tag filter is active.
- **Selected Image Tags** — shows tags for all selected images via `db.get_tags_for_images(image_ids)`. Tags present on only some of the selected images are shown at ~55% opacity with a `"tagname (N/total)"` label and a tooltip. The Remove button label updates dynamically: "Remove tag from all N images" for multi-selection.

The search bar has `QCompleter` autocomplete backed by a `QStringListModel` sourced from `db.get_all_tags_with_counts()` — DB-only tags, updated on every `refresh()`. No WD14 CSV is loaded for autocomplete. Stale `image_tags` rows (images deleted/moved outside the app) are surfaced by the count query joining only valid `image_tags` rows.

**Adding tags** is done via the RMB context menu in the gallery: Tags → Add tag… opens a `QInputDialog`. There is no inline add-tag input in the TagPanel itself.

`TagPanel.clear_search()` is called by `MainWindow` on every folder navigation to reset search state without triggering a double refresh (signals blocked during clear).

`db.get_tags_for_images(image_ids)` returns `(name, count)` rows via a single SQL query — count is how many of the given images have that tag.

### Media type definitions
Image extensions live in `src/core/image_scanner.py:SUPPORTED_EXTENSIONS`. Video extensions live in `src/core/thumbnail_cache.py:VIDEO_EXTENSIONS`. Both sets are combined in `folder_tree.py` and `gallery_view.py`.

### AI models
Models are downloaded from HuggingFace on first use and cached in `~/.cache/huggingface/`. All AI actions are manual-trigger only via the AI menu.

- **Classify Selected Images (Ctrl+R):** Stage 1 NSFW detection (~150 MB Falconsai) sorts images into SFW/NSFW folders; Stage 2 content tagging (~300 MB ViT) adds top-3 ImageNet labels as tags.
- **Tag with WD14… (Ctrl+T):** Tags images in-place using `SmilingWolf/wd-swinv2-tagger-v3` via the `wdtagger` package (~400 MB). Outputs general content tags, character tags, and a `rating:` tag (general/sensitive/questionable/explicit). Thresholds: general=0.35, character=0.9.
