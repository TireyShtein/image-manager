import sqlite3
import os
from datetime import datetime
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'imagemanager.db')


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
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
            "FROM tags t LEFT JOIN image_tags it ON t.id = it.tag_id "
            "GROUP BY t.id ORDER BY t.name"
        ).fetchall()


def add_tag_to_image(image_id: int, tag_name: str):
    tag_id = get_or_create_tag(tag_name)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)",
            (image_id, tag_id)
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


def get_images_by_tag(tag_name: str) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT i.* FROM images i JOIN image_tags it ON i.id = it.image_id "
            "JOIN tags t ON t.id = it.tag_id WHERE t.name = ? ORDER BY i.filename",
            (tag_name,)
        ).fetchall()


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
