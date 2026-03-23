# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Keeping CLAUDE.md Up to Date

After any session that includes a major update — new feature added, bug fixed, or 3+ files changed — update the relevant sections of this file to reflect the current state of the codebase. This includes:
- Architecture section: new components, changed signal flows, new attributes or patterns
- Any section describing behavior that was modified
- Do not add a changelog; update the existing descriptions in place so they stay accurate

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
  GalleryView         — paginated QListView (200 images/page); lazy viewport-driven thumbnail loading via QThreadPool
  TagPanel / AlbumPanel — right sidebar; read/write DB directly, emit filter signals
  ImageViewer         — modal QDialog for full-size view; opens videos via os.startfile()
  TriageImageViewer   — ImageViewer subclass; adds S/T/A/D shortcuts via QShortcut (WindowShortcut), HUD overlay, floating overlays for tag input / album picker; batches image_trashed(int) signal emissions on close

Core Layer (src/core/)
  database.py         — all SQLite access (functional API, no ORM)
  thumbnail_cache.py  — 256px JPEG cache in %APPDATA%/ImageManager/thumbs/, keyed by MD5(path)
  image_scanner.py    — recursive scan, populates DB
  file_ops.py         — move/copy/delete with DB path sync and trash support

AI Layer (src/ai/)
  WD14Worker          — QThread for WD14 tagging; emits progress/image_done(id,filename,tags)/error/finished_all(tagged,skipped,errors) signals
  wd14_tagger.py      — SmilingWolf/wd-swinv2-tagger-v3 via wdtagger; tags general/character/rating
  RatingSortWorker    — QThread for sorting images into SFW/NSFW folders by rating tags
```

### Threading model
- Thumbnail generation: `QThreadPool` (global) + `ThumbnailLoader(QRunnable)` per image — lazy, driven by viewport scroll (not eager start)
- Folder loading: `FolderLoaderRunnable(QRunnable)` — filesystem scan + `db.get_or_create_images_batch` run off the GUI thread; result returned via `FolderLoaderSignals.rows_ready(list, int, int)` (rows, token, recovered_count) with a token-based stale guard (`_load_token`) to discard results from superseded navigations
- Folder scan: `ScanWorker(QThread)` — runs `image_scanner.scan_folder` off the GUI thread; emits `progress(current, total)` and `finished_scan(added_count)`; no `processEvents()` anywhere
- Image decode: `_ImageLoadRunnable(QRunnable)` in `image_viewer.py` — decodes via `QImage` (thread-safe) off the GUI thread; GUI thread converts to `QPixmap` in the signal handler
- File operations: `FileOpWorker(QThread)` in `main_window.py` — runs move/copy loops off the GUI thread; emits `item_done(image_id)`, `item_error(image_id, msg)`, `progress(current, total)`, `finished_op(success_count, error_msgs)`
- WD14 tagging: `WD14Worker(QThread)` — one worker at a time, cancellable
- Rating sort: `RatingSortWorker(QThread)` — one worker at a time, cancellable
- All cross-thread communication uses `pyqtSignal`

### Database
SQLite with WAL journal mode, foreign keys enabled, and `PRAGMA busy_timeout = 5000` (prevents "database is locked" errors when background threads — `FolderLoaderRunnable`, `FileOpWorker` — write concurrently). Tables: `images`, `tags`, `image_tags`, `albums`, `album_images`, `ai_results`, `saved_filters`. All access goes through `src/core/database.py`. Connection opened per-call via `get_connection()` (context manager, Row factory).

The `saved_filters` table stores named tag filter presets: `id, name TEXT UNIQUE, tags TEXT (JSON array), mode TEXT ("AND"|"OR"), created_at TEXT`. Functions: `create_saved_filter(name, tags, mode)`, `get_all_saved_filters()`, `get_saved_filter(id)`, `delete_saved_filter(id)`, `rename_saved_filter(id, new_name)`. `create_saved_filter` raises `sqlite3.IntegrityError` on duplicate name (caller handles).

The `images` table has a `content_hash TEXT` column (SHA-256 of first 64KB + file size) used for tag recovery when images are moved outside the app. Added via `ALTER TABLE` migration in `init_db()`.

Indexes: `idx_image_path` on `images(path)`, `idx_tag_name` on `tags(name)`, `idx_image_tags_tag_id` on `image_tags(tag_id)` (added for tag-based filtering performance at scale), `idx_image_content_hash` on `images(content_hash)`, `idx_image_filename` on `images(filename)`.

### Gallery loading
`GalleryView.load_folder(folder)` is **fully asynchronous** — it increments `_load_token` and spawns a `FolderLoaderRunnable` that scans the filesystem and calls `db.get_or_create_images_batch` on a thread pool worker. Results arrive via `FolderLoaderSignals.rows_ready(rows, token, recovered)`; the slot discards stale results where `token != self._load_token` (rapid folder navigation safety). `self._loading` is set `True` immediately so the "No media" overlay is suppressed during the async load. No prior scan is needed to browse a folder — new files are auto-registered in the DB by the worker.

**Tag recovery for moved images:** When `get_or_create_images_batch` inserts a genuinely new path, it attempts to recover an orphaned DB record (preserving tags, albums, AI results) via two approaches: (1) content-hash match (SHA-256 of first 64KB + file size), (2) filename match with a single unambiguous orphan that has tags and whose old path no longer exists on disk. If recovery succeeds, the old record's path is updated and the blank new row is deleted. The `recovered_count` is propagated via `FolderLoaderSignals` → `GalleryView.tags_recovered(int)` signal → `MainWindow._on_tags_recovered` which appends the count to the status bar and refreshes the tag panel. Content hashes are backfilled opportunistically for existing records that lack one.

`load_paths(paths)` handles a list of individual files (used when files are selected directly in the tree) — calls `db.get_or_create_images_batch` directly (synchronous, list is typically small). Always resets `_show_folder_origin` to `False`. `load_images(rows, show_folder_origin=False)` loads an explicit list of DB rows (used by tag filter and album views) and returns a `LoadResult(shown, sfw_hidden, missing)` NamedTuple. Accepts `show_folder_origin: bool` which switches `DisplayRole` to render `"folder_name/filename"` — used by `MainWindow._on_tag_filter` to show provenance of cross-folder tag filter results.

### Gallery pagination
All loaded rows are handed to a `GalleryPager` instance (`_pager`), which stores them as plain dicts (`{id, path}`) and serves fixed-size pages. `PAGE_SIZE = 200`. Methods: `get_page(n)`, `remove(image_id)`, `all_items()`. `GalleryView` exposes `next_page()` / `prev_page()` / `_show_page(page)`. The `page_changed(page, page_count, total)` signal is wired to a pagination bar at the bottom of `MainWindow` showing "◀ Prev / Page X of Y (Z images) / Next ▶".

### Lazy thumbnail loading
`GalleryModel` stores only `display_pix` per item (no `source_pix`). Thumbnails are loaded on demand: `request_thumbnails(first_row, last_row)` queues `ThumbnailLoader` workers for the visible range ± `_PREFETCH_MARGIN = 40` rows. `_evict_offscreen(first_visible, last_visible)` frees pixmap memory for rows beyond `_EVICT_MARGIN = 80` rows from the viewport — items are cleared back to `None` and removed from `_queued`. Both are called from `GalleryView._on_scroll()` and `resizeEvent()`. A grey placeholder (`_get_placeholder(size)`) is shown until the disk cache returns a pixmap. On `set_display_size()`, all `display_pix` are cleared and re-fetched lazily from the disk cache (no `source_pix` to re-scale in memory).

**Folder origin label:** `GalleryModel` has a `_show_folder_origin: bool` flag (default `False`). When `True`, `DisplayRole` returns `"folder_name/filename"` instead of just the filename. `set_show_folder_origin(show)` toggles this and emits `dataChanged(DisplayRole)` to immediately re-render labels without reloading. Controlled by the `show_folder_origin` parameter of `load_images`; always cleared by `load_folder` and `load_paths`.

**Stale-result guard:** `GalleryModel` maintains `_thumb_token: int`, incremented on every `set_images()`. Each `ThumbnailLoader` captures the token at construction time and includes it in its `loaded(image_id, thumb_path, token)` signal. `_on_thumbnail_loaded` discards any result whose token doesn't match the current `_thumb_token`, preventing stale thumbnails from a previous folder from writing into the current model.

**Error overlay:** `GalleryModel` maintains `_error_ids: set[int]`. `mark_error(image_id)` adds an ID and emits `dataChanged` for that row. `data(DecorationRole)` applies `_apply_error_overlay(pix)` for errored items — a red wash + red border + white "!" badge painted onto a copy of the thumbnail. Cleared on `set_images()` (folder navigation). `GalleryView.mark_image_error(image_id)` is the public entry point, called by `MainWindow` when `FileOpWorker.item_error` fires.

**Hover metadata card:** `GalleryView` installs an `eventFilter` on its viewport (`viewport().setMouseTracking(True)`). On `MouseMove`, `_on_hover_move` maps the cursor to a model row via `indexAt()`; after 500ms (`_hover_timer`) `_show_hover_card` fires — queries filename, `os.path.getsize`, and `db.get_tags_for_images` (all synchronous/fast) then shows a `QFrame(ToolTip)` card beside the cursor with screen-boundary clamping. `QEvent.Type.ToolTip` events are swallowed to prevent native tooltip overlap. Card is hidden on scroll (`verticalScrollBar.valueChanged`), page change, folder load, context menu, and double-click. `GalleryModel.get_item(row)` is a bounds-safe public accessor used by the card.

### Gallery rating filter (SFW Mode)
`GalleryView` has a `_excluded_rating_tags: list[str]` attribute. `set_rating_filter(excluded)` sets which `rating:*` tags cause images to be hidden. Both `load_folder` and `load_images` run rows through `_apply_rating_filter()`, which calls `db.filter_out_images_with_tags(ids, excluded_tags)` to drop any image that has one of the excluded tags. Untagged images always pass through.

`MainWindow` exposes this via a checkable **View → SFW Mode** action (persisted in `QSettings`). When enabled, `rating:explicit` and `rating:questionable` images are hidden across all gallery views (folder, tag filter, album). Toggling calls `_reload_current_view()` which replays the current folder/tag/album load. Toggling also calls `self._tag_panel.set_sfw_mode(checked)` to disable/re-enable the corresponding tag checkboxes.

**`load_images` return type:** Returns a `LoadResult(NamedTuple)` with three fields: `shown` (rendered in gallery), `sfw_hidden` (on disk but removed by rating filter), `missing` (path not found on disk). Callers in `_on_tag_filter` and `_on_album_selected` build a multi-part suffix from these — e.g. `"45 images, 12 hidden by SFW Mode, 3 missing from disk"` — so the status bar never conflates SFW-hidden images with genuinely absent files.

### Thumbnail auto-sizing
`GalleryView` dynamically resizes thumbnails based on image count via `_compute_thumb_size(count)` and `_SIZE_TIERS` (≤4→300px, ≤12→240px, ≤30→180px, ≤80→140px, ≤200→110px, ≤500→85px, ≤700→78px, else→70px). `GalleryModel` stores only `display_pix` per item. On size change, all `display_pix` are cleared to `None` and re-fetched lazily from the disk cache (256px JPEG) via the viewport-driven loader.

Grid cell height is `thumb_px + label_px` where `label_px = max(20, thumb_px // 5)` — this scales the filename label area with thumbnail size. Filenames are hidden (DisplayRole returns `None`) below 140px to avoid overlap; tooltip still shows full path. Grid cell width is `thumb_px + 16` (8px per side). Size tier is computed from `len(rows)` and applied to the model *before* `set_images()` fires `endResetModel()`, preventing a size-pop on folder change.

**Density toggle:** `_show_page` applies a `_DENSITY_CONFIG[self._density]` multiplier (Compact=0.65, Comfortable=1.0, Spacious=1.40) to the auto-computed size, clamped to [60, 400] and rounded to even. Spacing also changes (4/8/12px). `set_density(mode)` early-returns if mode unchanged, then calls `_show_page(current_page)` to re-render. Exposed via **View → Density** submenu (`QActionGroup`, exclusive) in `MainWindow`; persisted to `QSettings` as `"density"`; restored before `_restore_last_folder()` in `__init__`.

### Gallery selection signal
`GalleryView` emits `selection_changed(list[int])` — a list of selected image IDs — whenever the selection changes. This is wired internally via `selectionModel().selectionChanged` → `_on_selection_changed` → `selection_changed.emit(...)`. `MainWindow` connects to `self._gallery.selection_changed` rather than reaching into `selectionModel()` directly, keeping the coupling at the domain level (IDs, not Qt model internals).

### Gallery empty state
`GalleryView` tracks a `_loading` flag. For async folder loads it is set `True` at the top of `load_folder()` (before the worker is even dispatched) and `_empty_overlay.hide()` is called immediately. `_loading` is cleared to `False` in `_on_all_loaded()` (connected to `GalleryModel._signals.all_loaded`), which then calls `_update_overlay_visibility()`.

**Onboarding overlay:** The empty state is a `_EmptyStateOverlay(QWidget)` parented to `GalleryView`, positioned to cover `viewport().rect()`. It contains: a folder icon + "No images here" heading, an "Open Folder…" button, a "drag a folder here" hint, and a collapsible "Recent Folders" section (up to 5 entries, validated by `os.path.isdir`). The overlay accepts drops (`setAcceptDrops(True)`) — dragging a folder onto it shows a blue border and fires `folder_dropped(str)` on drop.

`_update_overlay_visibility()` shows/hides the overlay based on `rowCount() == 0 and not self._loading`, re-sets geometry before show, and calls `raise_()` to keep it above the viewport. Called from: `_on_all_loaded`, `load_images`, `load_paths`, and `remove_image` (when gallery becomes empty via file operations).

Three new signals on `GalleryView`: `open_folder_requested` (Open Folder button), `recent_folder_requested(str)` (recent entry click), `folder_dropped(str)` (D&D). All three are handled by `MainWindow`. `load_images` no longer accepts `empty_text`/`empty_hint` parameters — the overlay handles all empty states uniformly.

### Folder tree scoping
`FolderTree.set_root(path)` restricts the tree's visible root to a single folder (`setRootIndex`). Called by `MainWindow._open_folder()` and `_restore_last_folder()`. `navigate_to(path)` scrolls/selects without changing the root. **Double-clicking a folder** in the tree calls `set_root()` to scope the tree to that folder. `select_files(paths)` highlights the given file paths in the tree using a 100ms deferred timer (required because `QFileSystemModel` populates asynchronously after `set_root()`); called by `MainWindow._open_location_in_tree()` after navigating to make revealed images visible.

### Persistent settings
`MainWindow` uses `QSettings("ImageManager", "ImageManager")` (Windows registry). Persisted keys:
- `last_folder` — reopened on launch if still on disk
- `sfw_mode` — SFW Mode toggle state (bool)
- `album_dialog_geometry` — album floating dialog position and size; restored on first creation, saved on dialog close (via `rejected` signal) and on app quit (via `MainWindow.closeEvent`)
- `splitter_state` — `QSplitter.saveState()` bytes; restored at end of `_build_ui()` with fallback `setSizes([220, 9999, 240])` if invalid/missing
- `left_panel_visible` / `right_panel_visible` — bool; panel visibility persisted alongside splitter state
- `density` — gallery density mode string (`"compact"` / `"comfortable"` / `"spacious"`); restored before first folder load
- `recent_folders` — `QStringList` of up to 5 recently opened folder paths; updated by `_add_recent_folder(path)` at every folder-open site; passed to `GalleryView.set_recent_folders()` at startup and after each update. Single-value deserialization quirk (`str` instead of `list`) handled at every read site.

### Resizable panes
`MainWindow._build_ui()` stores the splitter as `self._splitter`, left pane as `self._left_panel`, right pane as `self._right_panel`. Max widths: left 450px, right 400px (raised from 300/280). Center gallery is never collapsible (`setCollapsible(1, False)`); left and right are collapsible via drag. **View menu** has "Show Folder Tree" (Ctrl+1) and "Show Tag Panel" (Ctrl+2) checkable actions that call `_toggle_left_panel`/`_toggle_right_panel`. `_on_splitter_moved` keeps those checkmarks in sync when the user drags a pane to zero. All state saved in `closeEvent` before `super()`.

### Tag panel
The tag search bar is **debounced** — `textChanged` connects via `lambda _: self._search_timer.start()` to a 150ms single-shot `QTimer`, so rapid keystrokes (or paste) coalesce into one DB read + list rebuild. The lambda wrapper is required because `QTimer.start` has an `int` overload that PyQt6 would match against the `str` argument from `textChanged`, causing a TypeError.

`TagPanel` has two separate `QListWidget`s:
- **All Tags** — shows every tag in the DB grouped by category under non-clickable headers. Tags are checkable; checking one emits `tag_filter_changed(list[str], mode)` to filter the gallery. Category color-coding: `rating:*` tags in amber, all others in muted blue-gray. A "Clear filters (N)" ghost button shows the active count and clears all checkboxes.
- **Selected Image Tags** — shows tags for all selected images via `db.get_tags_for_images(image_ids)`. Tags present on only some of the selected images are shown at ~55% opacity with a `"tagname (N/total)"` label and a tooltip. The Remove button label updates dynamically: "Remove tag from all N images" for multi-selection.

**Tag category system** (prefix-based, no DB schema): `_tag_category(name)` returns `"rating"` for `rating:*` prefixed tags, `"general"` for everything else. Colors defined in `_CATEGORY_COLOR`, labels in `_CATEGORY_LABEL`.

**AND / OR mode toggle:** A checkable `QPushButton` in the control row switches `_filter_mode` between `"AND"` (images must have all selected tags) and `"OR"` (images may have any). Mode is included in every `tag_filter_changed` emission. `MainWindow._active_tag_mode` caches it so `_reload_current_view` replays with the correct logic.

**Sort toggle:** A second checkable button switches between count-descending (`_sort_by_count = True`) and alphabetical. Both sort within each category group.

**Signal:** `tag_filter_changed = pyqtSignal(list, str)` — `(tag_names, mode)` where mode is `"AND"` or `"OR"`.

**`itemChanged` vs `itemClicked`:** The global list uses `itemChanged` (not `itemClicked`) to detect checkbox toggles. `blockSignals(True/False)` wraps all programmatic item changes in `_refresh_global_list()` and `_on_item_changed` to prevent re-entrant signal loops. `_on_item_changed` also updates the item's foreground color immediately (with a `blockSignals` guard) rather than waiting for the next full refresh.

**Refresh split:** `refresh()` calls both `_refresh_global_list()` and `_refresh_selected_tags()`. On image selection change, only `_refresh_selected_tags()` is called — avoiding a full global list rebuild and 2 unnecessary DB queries on every gallery click. `_refresh_global_list()` is called on search, sort toggle, filter clear, folder nav, and tag add/remove.

**SFW Mode integration:** `TagPanel.set_sfw_mode(enabled)` is called by `MainWindow` when SFW Mode is toggled. When enabled, `rating:explicit` and `rating:questionable` tags render without a checkbox (flags = `ItemIsEnabled` only), in very dim amber, with a `Ø` suffix. Clicking them shows a `QToolTip` via `_on_item_clicked` explaining that SFW Mode is active. If either tag was in `_active_filter_tags` when SFW mode is enabled, it is automatically removed and `tag_filter_changed` is re-emitted. `_SFW_BLOCKED_TAGS = {"rating:explicit", "rating:questionable"}` is defined at module level.

The search bar has `QCompleter` autocomplete backed by a `QStringListModel` sourced from `db.get_all_tags_with_counts()` — DB-only tags, updated on every `_refresh_global_list()`. No WD14 CSV is loaded for autocomplete.

**Adding tags** is done via the RMB context menu in the gallery: Tags → Add tag… opens a `QInputDialog`. There is no inline add-tag input in the TagPanel itself.

**Global tag rename/delete:** Right-clicking any tag in the All Tags list opens a context menu with "Rename tag globally…" and "Delete tag globally…". Rename calls `db.rename_tag(old, new)` (`UPDATE tags SET name=?` — propagates to all `image_tags` via shared `tag_id`); shows a warning dialog on `IntegrityError` (duplicate name). Delete shows a confirmation with the affected image count then calls `db.delete_tag(name)` (`DELETE FROM tags` — `ON DELETE CASCADE` removes all `image_tags` rows). Both operations update `_active_filter_tags` in-place if the affected tag was an active filter, then call `refresh()` and re-emit `tag_filter_changed`. Context menu uses `Qt.ContextMenuPolicy.CustomContextMenu` connected to `_on_global_list_context_menu`; header items are skipped via the `UserRole` guard.

`TagPanel.clear_search()` is called by `MainWindow` on every folder navigation to reset search state (clears both search bar and active filters) without triggering a double refresh (signals blocked during clear). Note: `_clear_filter` does **not** clear the search bar — search text and active filter checkboxes are independent.

`db.get_tags_for_images(image_ids)` returns `(name, count)` rows via a single SQL query — count is how many of the given images have that tag.

### Status bar layout
```
| status_label (stretch) | selected_label || progress_counter | [progress bar] |
```
- `_status_label`: folder name, tag filter info, AI progress text
- `_selected_label`: "N selected"
- `_progress_counter`: `"current / total"` label, 80px fixed width, right-aligned, hidden when idle — shown only during WD14 tagging and rating sort (not during folder scan, which uses an indeterminate bar)
- `_progress`: `QProgressBar`, 200px fixed width, hidden when idle

`_set_counter_progress_visible(bool)` toggles both `_progress_counter` and `_progress` together so they never desync.

**Thumbnail progress coalescing:** `GalleryModel._signals.progress` can fire up to 200 times per page load. `GalleryView` interposes a 50ms single-shot `QTimer` (`_loading_flush_timer`) — `_on_thumb_progress_raw` stores the latest `(loaded, total)` pair and either flushes immediately (when complete) or starts the timer if not already running. `_flush_thumb_progress` emits `thumbnails_loading` at most every 50ms. The timer is stopped in `_load_rows()` to prevent stale page-A values from firing after page-B loads.

### ImageViewer async decode
`ImageViewer._load_image()` calls `_refresh_tags()` first (synchronous DB query, fast), then shows a "Loading…" scene, disables nav buttons, and submits a `_ImageLoadRunnable` to the global `QThreadPool`. The runnable creates a `QImage(path)` on the worker thread (`QImage` is thread-safe; `QPixmap` is not). On `_ImageLoadSignals.loaded(img, path)`, the GUI thread checks `path != self.image_path` (stale guard for rapid navigation), checks `img.isNull()` (decode failure → "Failed to load image" text), then calls `QPixmap.fromImage(img)` and builds the scene. `self._load_signals` is created once in `__init__` and reused across all navigations.

**Tag strip:** `_tags_label` is a word-wrapped `QLabel` (max height 46px) inserted between the graphics view and the nav bar. `_refresh_tags()` queries `db.get_tags_for_images([self.image_id])`, sorts rating tags first then others alphabetically, and sets the label text as `"tag1  ·  tag2  ·  …"`. Shows "No tags" for untagged images. Called at the start of `_load_image()` so tags appear immediately — before the async image decode finishes.

**Nav button boundary state:** `_update_nav_buttons()` disables Prev when `_current_index == 0` and Next when `_current_index == len(_all_images) - 1`. `_navigate(delta)` uses a bounds guard (early return) instead of modulo wrap — keyboard arrows at the boundary do nothing. `_set_nav_enabled(True)` calls `_update_nav_buttons()` so boundary state is always correct after async decode completes.

### File operations
Move and copy run on `FileOpWorker(QThread)` — the GUI thread never blocks. Both `_move_images` and `_copy_images` show a `QMessageBox.warning` confirmation dialog (showing count + destination path) before dispatching the worker; Move uses `QMessageBox.warning` (warning icon, No as default) to signal irreversibility. `item_done(image_id)` is connected to `self._gallery.remove_image` for moves (removes the thumbnail immediately). `finished_op(success, errors)` writes the outcome to `_status_label` and shows a warning dialog if any errors occurred. `_scan_folder` uses `ScanWorker(QThread)` — no `processEvents()` anywhere; progress signals update the status bar and progress bar while the UI stays fully responsive.

### Delete safety
"Delete Permanently" in the context menu is separated from "Delete (Trash)" by a `menu.addSeparator()` and carries a `SP_MessageBoxWarning` icon to prevent misclicks. `_delete_images` tracks a `deleted` counter and writes the outcome ("Deleted N image(s) — M failed") to the status bar after each operation.

### Triage Mode
`TriageImageViewer(ImageViewer)` — subclass launched via **View → Triage Mode… (Ctrl+K)** or "Triage from here…" in the gallery context menu. Single-key actions via `QShortcut(WindowShortcut)` (bypasses `ZoomableGraphicsView` focus):
- **S** — adds `star` tag via `db.add_tags_to_image_batch`, refreshes tag strip
- **T** — opens floating tag-input overlay (`QFrame` + `QLineEdit` with `QCompleter`); Enter applies tag, Esc dismisses
- **A** — opens album picker overlay (`QFrame` + `QListWidget`); double-click or Enter adds image to album, Esc dismisses
- **D** — calls `file_ops.delete_image(use_trash=True)`, pops from `_all_images`, auto-advances; when last image deleted, emits all accumulated `image_trashed` signals then calls `accept()`

All overlays disabled S/T/A/D shortcuts while showing (`_set_shortcuts_enabled(False)`). `_dismiss_overlays()` re-enables them and returns focus to `_view`. HUD `QLabel` parented directly to dialog, positioned at bottom via `resizeEvent`/`showEvent`. `_flash_hud(msg)` shows feedback text for 1.5s then resets to the legend.

**Batch signal emission:** `image_trashed` signals are collected in `_trashed_ids` and emitted in `closeEvent` (normal close) or immediately before `accept()` (last-image-trashed path), because `accept()` does not trigger `closeEvent` in PyQt6. `MainWindow._on_triage_image_trashed` calls `gallery.remove_image(image_id)` for each.

### Drag-and-Drop
Gallery thumbnails are draggable via `setDragDropMode(DragOnly)` on `GalleryView`. **Critical:** `GalleryModel` must override `flags()` to return `ItemIsEnabled | ItemIsSelectable | ItemIsDragEnabled` — without `ItemIsDragEnabled`, Qt never calls `startDrag` and falls back to rubber-band selection instead. `startDrag` packs selected image IDs into custom MIME type `"application/x-imagemanager-ids"` (JSON `[id, ...]`). Single-image drag uses the loaded thumbnail (scaled 64×64 if `display_pix` is not None); multi-image drag shows a 44×44 blue count badge painted with `QPainter`.

**Drop onto album (`album_panel.py`):** Event filter installed on `self._list.viewport()`. **Critical:** `DragEnter` must accept unconditionally for the correct MIME type — checking item position in `DragEnter` and ignoring causes Qt to stop delivering all subsequent `DragMove` events to the widget entirely. Position checking (accept/ignore) belongs only in `DragMove` and `Drop`. `DragMove` highlights the hovered item via `_set_album_highlight` (`QColor(80, 130, 255, 110)` background); clears on `DragLeave` and `Drop`. `Drop` calls `db.add_image_to_album` in a loop, calls `self.refresh()`, then emits `images_added_to_album = pyqtSignal(int, str)`. `MainWindow._on_images_added_to_album` writes the count + album name to `_status_label`. Smart Collections (`_collections_list`) has no drop handling.

**Drop onto folder tree (`folder_tree.py`):** `FolderTree` subclass overrides `dragEnterEvent`/`dragMoveEvent`/`dragLeaveEvent`/`dropEvent` directly. `QFileSystemModel.setReadOnly(True)` disables the model's own file-move-on-drop behavior. `dragEnterEvent` saves `self._pre_drag_index = self.currentIndex()` before any highlight changes. `dragMoveEvent` highlights the hovered folder using `selectionModel().blockSignals(True)` + `setCurrentIndex(index, ClearAndSelect)` + `blockSignals(False)` + `self.viewport().update()`. **Critical:** `blockSignals` suppresses Qt's internal `selectionChanged` listener (preventing gallery reload), but also suppresses the view's repaint notification — `viewport().update()` is required or the blue highlight never appears. A same-index guard (`if index != self.currentIndex()`) skips unnecessary repaints when hovering over the same row. `_restore_pre_drag_selection()` helper (called from `dragLeaveEvent` and `dropEvent`) restores the pre-drag selection using the same `blockSignals + ClearAndSelect + viewport().update()` pattern. `dropEvent` emits `images_dropped_on_folder = pyqtSignal(list, str)`. `MainWindow._on_dnd_folder_drop` filters same-folder drops (via `os.path.normpath` on both sides for Windows path separator safety), shows `QMessageBox.question` with full destination path and album-membership note when in album/collection view, then calls `_start_file_op("move", ...)`.

### Smart Collections
`AlbumPanel` has a second `QListWidget` (`_collections_list`, max 150px height) under a "Smart Collections" header. New signal: `collection_selected = pyqtSignal(int)` (filter_id). Items show collection name as label, filter details in tooltip. RMB context menu: Rename / Delete. `refresh()` calls `_refresh_albums()` + `_refresh_collections()`.

Saving: **View → Save Filter as Collection…** (enabled when `_active_tag_filter` is non-empty) prompts for a name and calls `db.create_saved_filter(name, tags, mode)`. The action is disabled when navigating to a folder, album, or collection view.

`MainWindow._on_collection_selected(filter_id)`: sets `_active_collection_id`, clears `_active_tag_filter`/`_active_album_id`, re-runs `db.get_images_by_tags_and/or(tags)` with saved mode, loads into gallery with `show_folder_origin=True`. `_reload_current_view()` handles collection case first (before album and tag filter). `_active_collection_id` is cleared at all folder-open and filter-change sites.

### Album panel rename safety
Album display text is `f"{name} ({count})"`. The rename/delete handlers extract the name with `.rsplit(" (", 1)[0]` (not `.split`) to correctly handle album names that contain ` (` — e.g. `"Summer (2024)"` round-trips correctly.

### Media type definitions
Image extensions live in `src/core/image_scanner.py:SUPPORTED_EXTENSIONS`. Video extensions live in `src/core/thumbnail_cache.py:VIDEO_EXTENSIONS`. Both sets are combined in `folder_tree.py` and `gallery_view.py`.

### AI models
Models are downloaded from HuggingFace on first use and cached in `~/.cache/huggingface/`. All AI actions are manual-trigger only via the AI menu.

- **Tag with WD14… (Ctrl+T):** Tags selected images in-place using `SmilingWolf/wd-swinv2-tagger-v3` via the `wdtagger` package (~400 MB). Outputs general content tags, character tags, and a `rating:` tag (`rating:general`, `rating:sensitive`, `rating:questionable`, `rating:explicit`). Thresholds: general=0.35, character=0.9. **Resumable:** `WD14Worker` skips images that already have any `rating:*` tag (via `db.get_image_ids_with_rating_tag()`), so cancelling and restarting a large batch is safe. `self._act_wd14` is disabled while the worker runs and re-enabled in both `_on_wd14_finished` and `_on_wd14_thread_finished` (safety net). **Signals:** `image_done(image_id, filename, tags)` — filename passed directly from pre-fetched `image_map` (no GUI-thread DB call); `finished_all(tagged, skipped, errors)` — rich completion summary shown in status bar (e.g. `"WD14 done: 8 tagged, 3 skipped (already tagged), 1 error."`). Per-image status shown in `_status_label` during run.

- **Sort into SFW/NSFW by Tags… (AI menu):** Batch-moves images in the current folder into user-chosen SFW and NSFW destination folders based on their existing WD14 rating tags. `rating:general` and `rating:sensitive` → SFW folder; `rating:explicit` and `rating:questionable` → NSFW folder; untagged images are left in place. Shows a preview dialog with counts before asking for destination folders. Runs on `RatingSortWorker(QThread)`, cancellable. After completion, reloads the current folder and refreshes the folder tree. `self._act_sort` is disabled while the worker runs and re-enabled in both `_on_sort_finished` and `_on_sort_thread_finished` (safety net).

### Key database functions
- `db.get_or_create_images_batch(paths)` — returns `(rows, recovered_count)` tuple; bulk register + fetch images in one transaction (chunked to 500 paths); recovers orphaned records for moved images via content-hash or filename matching
- `db.compute_content_hash(filepath)` — SHA-256 of first 64KB + file size; returns hex digest or `None` on error
- `db.get_images_batch(image_ids)` — fetch multiple images in one query, returns `{id: Row}` dict
- `db.add_tags_to_image_batch(image_id, tag_names)` — bulk add tags in one transaction
- `db.get_images_by_tag(tag_name)` — all images with a given tag
- `db.get_images_by_tags_and(tag_names)` — images that have ALL of the given tags (AND logic); delegates to `get_images_by_tag` for single-tag case
- `db.get_images_by_tags_or(tag_names)` — images that have ANY of the given tags (OR logic); uses `GROUP BY i.id` to avoid duplicate rows
- `db.filter_out_images_with_tags(image_ids, excluded_tags)` — returns subset of IDs with none of the excluded tags (used by SFW Mode)
- `db.get_image_ids_with_rating_tag(image_ids)` — set of IDs that already have a `rating:*` tag (used by WD14 resumability)
- `db.get_images_with_ratings_in_folder(folder)` — returns `(id, path, rating)` rows for images directly in a folder (non-recursive); `rating` is the `rating:*` tag or `None` (used by rating sort preview and worker)
- `db.rename_tag(old_name, new_name)` — `UPDATE tags SET name=?`; raises `sqlite3.IntegrityError` if new name already exists
- `db.delete_tag(tag_name)` — `DELETE FROM tags WHERE name=?`; cascade removes all `image_tags` rows for that tag
