import sqlite3
import os
from datetime import datetime
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'imagemanager.db')


def db_exists() -> bool:
    return os.path.isfile(os.path.abspath(DB_PATH))


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                width INTEGER,
                height INTEGER,
                file_size INTEGER,
                date_added TEXT,
                date_modified TEXT
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS image_tags (
                image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
                tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (image_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS album_images (
                album_id INTEGER REFERENCES albums(id) ON DELETE CASCADE,
                image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
                position INTEGER DEFAULT 0,
                PRIMARY KEY (album_id, image_id)
            );

            CREATE TABLE IF NOT EXISTS ai_results (
                image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                label TEXT NOT NULL,
                confidence REAL NOT NULL,
                classified_at TEXT,
                PRIMARY KEY (image_id, stage)
            );

            CREATE INDEX IF NOT EXISTS idx_image_path ON images(path);
            CREATE INDEX IF NOT EXISTS idx_tag_name ON tags(name);
            CREATE INDEX IF NOT EXISTS idx_image_tags_tag_id ON image_tags(tag_id);
        """)


# --- Images ---

def add_image(path: str, filename: str, width: int = None, height: int = None,
              file_size: int = None) -> int:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO images (path, filename, width, height, file_size, date_added, date_modified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path, filename, width, height, file_size, now, now)
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM images WHERE path = ?", (path,)).fetchone()
        return row["id"]


def get_image(image_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()


def get_image_by_path(path: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM images WHERE path = ?", (path,)).fetchone()


def get_images_batch(image_ids: list[int]) -> dict:
    """Fetch multiple images by ID in one query. Returns {id: Row}."""
    if not image_ids:
        return {}
    placeholders = ",".join("?" * len(image_ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM images WHERE id IN ({placeholders})",
            image_ids,
        ).fetchall()
        return {row["id"]: row for row in rows}


def get_images_in_folder(folder: str) -> list:
    folder = folder.rstrip('/\\') + os.sep
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM images WHERE path LIKE ? ORDER BY filename",
            (folder + '%',)
        ).fetchall()


def update_image_path(image_id: int, new_path: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE images SET path = ?, filename = ?, date_modified = ? WHERE id = ?",
            (new_path, os.path.basename(new_path), datetime.now().isoformat(), image_id)
        )


def delete_image(image_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))


def get_all_image_paths() -> list:
    with get_connection() as conn:
        rows = conn.execute("SELECT id, path FROM images ORDER BY filename").fetchall()
        return [(r["id"], r["path"]) for r in rows]


def get_or_create_images_batch(paths: list[str]) -> list:
    """Register many images in one transaction and return all rows.

    Uses INSERT OR IGNORE so existing rows are left untouched, then
    fetches every row whose path is in *paths*.  Paths are processed in
    chunks of 500 to stay well within SQLite's variable limit.
    """
    if not paths:
        return []
    now = datetime.now().isoformat()
    CHUNK = 500
    all_rows = []
    with get_connection() as conn:
        for i in range(0, len(paths), CHUNK):
            chunk = paths[i:i + CHUNK]
            conn.executemany(
                "INSERT OR IGNORE INTO images (path, filename, width, height, file_size, date_added, date_modified) "
                "VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
                [(p, os.path.basename(p), now, now) for p in chunk],
            )
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT * FROM images WHERE path IN ({placeholders}) ORDER BY filename",
                chunk,
            ).fetchall()
            all_rows.extend(rows)
    # Re-sort all rows by filename since they came from separate chunks
    return sorted(all_rows, key=lambda r: r["filename"].lower())


def cleanup_stale_images() -> int:
    rows = get_all_image_paths()
    stale_ids = [img_id for img_id, path in rows if not os.path.isfile(path)]
    if stale_ids:
        with get_connection() as conn:
            conn.executemany("DELETE FROM images WHERE id = ?", [(i,) for i in stale_ids])
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)"
        )
    return len(stale_ids)


# --- Tags ---

def get_or_create_tag(name: str) -> int:
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        return row["id"]


def get_all_tags() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM tags ORDER BY name").fetchall()


def get_all_tags_with_counts() -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT t.name, COUNT(it.image_id) as count "
            "FROM tags t JOIN image_tags it ON t.id = it.tag_id "
            "GROUP BY t.id ORDER BY t.name"
        ).fetchall()


def search_tags_with_counts(query: str) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT t.name, COUNT(it.image_id) as count "
            "FROM tags t JOIN image_tags it ON t.id = it.tag_id "
            "WHERE t.name LIKE ? "
            "GROUP BY t.id ORDER BY t.name",
            (f"%{query}%",)
        ).fetchall()


def add_tag_to_image(image_id: int, tag_name: str):
    tag_id = get_or_create_tag(tag_name)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)",
            (image_id, tag_id)
        )


def add_tags_to_image_batch(image_id: int, tag_names: list[str]):
    """Add multiple tags to an image in a single transaction."""
    if not tag_names:
        return
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO tags (name) VALUES (?)",
            [(name,) for name in tag_names],
        )
        placeholders = ",".join("?" * len(tag_names))
        tag_rows = conn.execute(
            f"SELECT id, name FROM tags WHERE name IN ({placeholders})",
            tag_names,
        ).fetchall()
        conn.executemany(
            "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)",
            [(image_id, row["id"]) for row in tag_rows],
        )


def remove_tag_from_image(image_id: int, tag_name: str):
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        if row:
            conn.execute(
                "DELETE FROM image_tags WHERE image_id = ? AND tag_id = ?",
                (image_id, row["id"])
            )


def get_tags_for_image(image_id: int) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT t.name FROM tags t JOIN image_tags it ON t.id = it.tag_id "
            "WHERE it.image_id = ? ORDER BY t.name",
            (image_id,)
        ).fetchall()
        return [r["name"] for r in rows]


def get_tags_for_images(image_ids: list) -> list:
    """Returns (name, count) rows for all tags across the given images, sorted by name."""
    if not image_ids:
        return []
    placeholders = ",".join("?" * len(image_ids))
    with get_connection() as conn:
        return conn.execute(
            f"SELECT t.name, COUNT(it.image_id) as count "
            f"FROM tags t JOIN image_tags it ON t.id = it.tag_id "
            f"WHERE it.image_id IN ({placeholders}) "
            f"GROUP BY t.id ORDER BY t.name",
            image_ids
        ).fetchall()


def get_images_with_ratings_in_folder(folder: str) -> list:
    """Returns rows (id, path, rating) for images directly inside folder (non-recursive).
    rating is the rating:* tag name, or None if untagged."""
    folder_prefix = folder.rstrip('/\\') + os.sep
    with get_connection() as conn:
        return conn.execute(
            "SELECT i.id, i.path, "
            "  (SELECT t2.name FROM tags t2 "
            "   JOIN image_tags it2 ON t2.id = it2.tag_id "
            "   WHERE it2.image_id = i.id AND t2.name LIKE 'rating:%' LIMIT 1) AS rating "
            "FROM images i "
            "WHERE i.path LIKE ? AND i.path NOT LIKE ?",
            (folder_prefix + '%', folder_prefix + '%' + os.sep + '%'),
        ).fetchall()


def filter_out_images_with_tags(image_ids: list[int], excluded_tags: list[str]) -> list[int]:
    """Return subset of image_ids that have NONE of the excluded_tags."""
    if not image_ids or not excluded_tags:
        return image_ids
    placeholders_ids = ",".join("?" * len(image_ids))
    placeholders_tags = ",".join("?" * len(excluded_tags))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT it.image_id FROM image_tags it "
            f"JOIN tags t ON t.id = it.tag_id "
            f"WHERE it.image_id IN ({placeholders_ids}) AND t.name IN ({placeholders_tags})",
            image_ids + excluded_tags,
        ).fetchall()
        excluded_set = {row[0] for row in rows}
        return [iid for iid in image_ids if iid not in excluded_set]


def get_image_ids_with_rating_tag(image_ids: list[int]) -> set[int]:
    """Return set of image_ids that already have any rating:* tag."""
    if not image_ids:
        return set()
    placeholders = ",".join("?" * len(image_ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT it.image_id FROM image_tags it "
            f"JOIN tags t ON t.id = it.tag_id "
            f"WHERE it.image_id IN ({placeholders}) AND t.name LIKE 'rating:%'",
            image_ids,
        ).fetchall()
        return {row[0] for row in rows}


def get_images_by_tag(tag_name: str) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT i.* FROM images i JOIN image_tags it ON i.id = it.image_id "
            "JOIN tags t ON t.id = it.tag_id WHERE t.name = ? ORDER BY i.filename",
            (tag_name,)
        ).fetchall()


def get_images_by_tags_and(tag_names: list[str]) -> list:
    """Return images that have ALL of the given tags (AND logic)."""
    if not tag_names:
        return []
    if len(tag_names) == 1:
        return get_images_by_tag(tag_names[0])
    placeholders = ",".join("?" * len(tag_names))
    with get_connection() as conn:
        return conn.execute(
            f"SELECT i.* FROM images i "
            f"JOIN image_tags it ON i.id = it.image_id "
            f"JOIN tags t ON t.id = it.tag_id "
            f"WHERE t.name IN ({placeholders}) "
            f"GROUP BY i.id "
            f"HAVING COUNT(DISTINCT t.name) = ? "
            f"ORDER BY i.filename",
            tag_names + [len(tag_names)],
        ).fetchall()


def get_images_by_tags_or(tag_names: list[str]) -> list:
    """Return images that have ANY of the given tags (OR logic)."""
    if not tag_names:
        return []
    if len(tag_names) == 1:
        return get_images_by_tag(tag_names[0])
    placeholders = ",".join("?" * len(tag_names))
    with get_connection() as conn:
        return conn.execute(
            f"SELECT i.* FROM images i "
            f"JOIN image_tags it ON i.id = it.image_id "
            f"JOIN tags t ON t.id = it.tag_id "
            f"WHERE t.name IN ({placeholders}) "
            f"GROUP BY i.id "
            f"ORDER BY i.filename",
            tag_names,
        ).fetchall()


def rename_tag(old_name: str, new_name: str):
    """Rename a tag globally. Raises sqlite3.IntegrityError if new_name already exists."""
    with get_connection() as conn:
        conn.execute("UPDATE tags SET name = ? WHERE name = ?", (new_name, old_name))


def delete_tag(tag_name: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM tags WHERE name = ?", (tag_name,))


# --- Albums ---

def create_album(name: str, description: str = "") -> int:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO albums (name, description, created_at) VALUES (?, ?, ?)",
            (name, description, now)
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM albums WHERE name = ?", (name,)).fetchone()
        return row["id"]


def get_all_albums() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM albums ORDER BY name").fetchall()


def get_album(album_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()


def rename_album(album_id: int, new_name: str):
    with get_connection() as conn:
        conn.execute("UPDATE albums SET name = ? WHERE id = ?", (new_name, album_id))


def delete_album(album_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))


def add_image_to_album(album_id: int, image_id: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO album_images (album_id, image_id) VALUES (?, ?)",
            (album_id, image_id)
        )


def remove_image_from_album(album_id: int, image_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM album_images WHERE album_id = ? AND image_id = ?",
            (album_id, image_id)
        )


def get_images_in_album(album_id: int) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT i.* FROM images i JOIN album_images ai ON i.id = ai.image_id "
            "WHERE ai.album_id = ? ORDER BY ai.position, i.filename",
            (album_id,)
        ).fetchall()


def get_albums_for_image(image_id: int) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT a.id, a.name FROM albums a JOIN album_images ai ON a.id = ai.album_id "
            "WHERE ai.image_id = ? ORDER BY a.name",
            (image_id,)
        ).fetchall()
        return [(r["id"], r["name"]) for r in rows]


def get_album_image_count(album_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM album_images WHERE album_id = ?", (album_id,)
        ).fetchone()
        return row["cnt"]


# --- AI Results ---

def save_ai_result(image_id: int, stage: str, label: str, confidence: float):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ai_results (image_id, stage, label, confidence, classified_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (image_id, stage, label, confidence, now)
        )


def get_ai_result(image_id: int, stage: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM ai_results WHERE image_id = ? AND stage = ?",
            (image_id, stage)
        ).fetchone()
