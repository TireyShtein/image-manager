# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Keeping CLAUDE.md Up to Date

After any session that includes a major update — new feature added, bug fixed, or 3+ files changed — update the relevant sections of this file to reflect the current state of the codebase. Update existing descriptions in place; do not add a changelog.

## Running the App

```bash
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

> **Windows DLL gotcha:** `import torch` must appear before any PyQt6 import in `main.py` — already handled at `main.py:2`.

## Architecture

Three layers communicating via PyQt6 signals:

```
UI Layer (src/ui/)
  MainWindow          — layout host, signal wiring, file/AI action handlers
  FolderTree          — QFileSystemModel tree; emits folder_selected / files_selected
  GalleryView         — paginated QListView (200 images/page); lazy viewport-driven thumbnail loading
  TagPanel / AlbumPanel — right sidebar; read/write DB directly, emit filter signals
  ImageViewer         — modal QDialog for full-size view; opens videos via os.startfile()
  TriageImageViewer   — ImageViewer subclass; S/T/A/D shortcuts, HUD overlay, tag/album overlays

Core Layer (src/core/)
  database.py         — all SQLite access (functional API, no ORM)
  thumbnail_cache.py  — 256px JPEG cache in %APPDATA%/ImageManager/thumbs/
  image_scanner.py    — recursive scan, populates DB
  file_ops.py         — move/copy/delete with DB path sync and trash support

AI Layer (src/ai/)
  WD14Worker          — QThread for WD14 tagging
  wd14_tagger.py      — SmilingWolf/wd-swinv2-tagger-v3 via wdtagger; general/character/rating tags
  RatingSortWorker    — QThread for sorting images into SFW/NSFW folders by rating tags
```

### Threading model
All background work runs off the GUI thread via `QThread` or `QThreadPool`; all cross-thread communication uses `pyqtSignal`.
- **Thumbnail loading** — `ThumbnailLoader(QRunnable)` per image, lazy/viewport-driven
- **Folder loading** — `FolderLoaderRunnable(QRunnable)`; token-based stale guard discards superseded navigations
- **Folder scan** — `ScanWorker(QThread)`; emits `progress` and `finished_scan`
- **Image decode** — `_ImageLoadRunnable(QRunnable)`; `QImage` decoded off-thread, converted to `QPixmap` on GUI thread
- **File operations** — `FileOpWorker(QThread)`; emits `item_done`, `item_error`, `progress`, `finished_op`
- **WD14 tagging / Rating sort** — one `QThread` worker at a time, cancellable

Short-lived QThreads (`ScanWorker`, `FileOpWorker`, `WD14Worker`, `RatingSortWorker`) call `close_connection()` in a `finally` block to release the DB file handle. QThreadPool workers do not — their connections persist across reuse intentionally.

## Database

SQLite with WAL journal mode, foreign keys, `PRAGMA busy_timeout = 5000`. All access via `src/core/database.py`.

**Tables:** `images`, `tags`, `image_tags`, `albums`, `album_images`, `ai_results`, `saved_filters`

**Indexes:** `images(path)`, `tags(name)`, `image_tags(tag_id)`, `images(content_hash)`, `images(filename)`

**Connection model:** `get_connection()` is a `@contextmanager` backed by `threading.local()`. Each thread gets one connection created on first use and reused for all subsequent calls. Commits on normal exit, rolls back on exception. Usage: `with get_connection() as conn:`.

**Tag recovery:** When a new path is inserted, the DB tries to recover an orphaned record (preserving tags/albums/AI results) via (1) SHA-256 content-hash match or (2) unambiguous filename match whose old path no longer exists.

**`saved_filters` table:** Stores named tag filter presets — `id, name TEXT UNIQUE, tags TEXT (JSON), mode TEXT ("AND"|"OR"), created_at TEXT`.

### Key database functions
- `get_or_create_images_batch(paths)` — bulk register + fetch; returns `(rows, recovered_count)`; chunked to 500 paths
- `get_images_batch(image_ids)` — fetch multiple images in one query; returns `{id: Row}`
- `add_tags_to_image_batch(image_id, tag_names)` — bulk add tags to one image in one transaction
- `add_tag_to_images_batch(image_ids, tag_name)` — add one tag to many images in one transaction
- `remove_tag_from_images_batch(image_ids, tag_name)` — remove one tag from many images; chunked at 900 IDs
- `get_images_by_tags_and(tag_names)` — images with ALL given tags
- `get_images_by_tags_or(tag_names)` — images with ANY given tag
- `filter_out_images_with_tags(image_ids, excluded_tags)` — used by SFW Mode
- `get_image_ids_with_rating_tag(image_ids)` — set of IDs that already have a `rating:*` tag
- `get_all_tags_with_counts()` — all tags with per-tag image counts
- `get_all_albums_with_counts()` — all albums with image counts (single LEFT JOIN GROUP BY)
- `rename_tag(old, new)` — propagates via shared `tag_id`; raises `IntegrityError` on duplicate
- `delete_tag(name)` — CASCADE removes all `image_tags` rows

## Main Features

### Gallery
- Paginated view (200 images/page) with lazy thumbnail loading driven by viewport scroll
- Thumbnails auto-size by image count; three density modes (Compact / Comfortable / Spacious) via View menu
- Hover card shows filename, file size, and tags after 500ms
- SFW Mode (View menu) hides `rating:explicit` and `rating:questionable` images across all views
- Drag thumbnails onto albums or folders; drop folders onto the gallery to open them

### Tag Panel
- Lists all tags grouped by category (rating / general) with per-tag image counts
- Checkboxes filter the gallery; AND/OR mode toggle; count-desc or alphabetical sort
- Tag search with debounce (150ms) and `QCompleter` autocomplete; 0 DB calls on search keystroke
- Right-click any tag to rename or delete it globally
- Add tags via gallery RMB context menu → Tags → Add tag…

### Albums & Smart Collections
- Albums panel: create, rename, delete; drag images onto an album to add them
- Smart Collections: saved tag filter presets (name + tag list + AND/OR mode); shown under a separate header

### Triage Mode (`Ctrl+K`)
Full-screen image review with single-key shortcuts:
- **S** — star tag, **T** — tag input overlay, **A** — album picker overlay, **D** — trash

### Image Viewer
- Zoomable `QGraphicsView` with async image decode off the GUI thread
- Detail panel shows filename, path, dimensions, file size, albums, and tags
- Keyboard navigation with boundary guards (no wrap-around)

### AI
- **WD14 tagging (`Ctrl+T`)** — `SmilingWolf/wd-swinv2-tagger-v3`; outputs general, character, and rating tags; resumable (skips already-tagged images)
- **Sort SFW/NSFW** — batch-moves images by existing rating tags into user-chosen folders; preview dialog shown before execution

### File Operations
- Move and copy run on `FileOpWorker(QThread)`; confirmation dialog before dispatch
- Move removes thumbnails from gallery immediately via `item_done` signal
- Trash and permanent delete available; permanent delete has warning icon separator to prevent misclicks

### Persistent Settings
`QSettings("ImageManager", "ImageManager")` persists: `last_folder`, `sfw_mode`, `density`, `splitter_state`, `left_panel_visible`, `right_panel_visible`, `album_dialog_geometry`, `recent_folders` (up to 5).
