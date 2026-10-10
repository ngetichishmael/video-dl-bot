from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    mode TEXT NOT NULL,
    title TEXT,
    site TEXT,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    has_audio INTEGER NOT NULL DEFAULT 0,
    file_id TEXT,
    audio_file_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS downloads_user_url ON downloads (user_id, url, mode);
CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    download_id INTEGER NOT NULL REFERENCES downloads (id) ON DELETE CASCADE,
    PRIMARY KEY (chat_id, message_id)
);
"""


@dataclass(frozen=True, slots=True)
class Download:
    id: int
    url: str
    mode: str
    title: str | None
    site: str | None
    media_type: str
    size_bytes: int
    has_audio: bool
    file_id: str | None
    audio_file_id: str | None
    note: str | None
    created_at: str


def _row(row: sqlite3.Row) -> Download:
    return Download(
        id=row["id"],
        url=row["url"],
        mode=row["mode"],
        title=row["title"],
        site=row["site"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        has_audio=bool(row["has_audio"]),
        file_id=row["file_id"],
        audio_file_id=row["audio_file_id"],
        note=row["note"],
        created_at=row["created_at"],
    )


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def reserve(
        self,
        *,
        user_id: int,
        url: str,
        mode: str,
        title: str | None,
        site: str | None,
        media_type: str,
        size_bytes: int,
        has_audio: bool,
    ) -> int:
        """Create the row before sending so the id can be shown on the media."""
        created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO downloads (user_id, url, mode, title, site, media_type,"
                " size_bytes, has_audio, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, url, mode, title, site, media_type, size_bytes,
                    int(has_audio), created,
                ),
            )
        return cursor.lastrowid

    def finalize(
        self,
        download_id: int,
        *,
        file_id: str | None,
        audio_file_id: str | None,
        chat_id: int,
        message_ids: list[int],
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE downloads SET file_id = ?, audio_file_id = ? WHERE id = ?",
                (file_id, audio_file_id, download_id),
            )
            db.executemany(
                "INSERT OR REPLACE INTO messages (chat_id, message_id, download_id)"
                " VALUES (?, ?, ?)",
                [(chat_id, mid, download_id) for mid in message_ids],
            )

    def map_messages(self, chat_id: int, download_id: int, message_ids: list[int]) -> None:
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO messages (chat_id, message_id, download_id)"
                " VALUES (?, ?, ?)",
                [(chat_id, mid, download_id) for mid in message_ids],
            )

    def find_cached(self, user_id: int, url: str, mode: str) -> Download | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM downloads WHERE user_id = ? AND url = ? AND mode = ?"
                " AND file_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                (user_id, url, mode),
            ).fetchone()
        return _row(row) if row else None

    def by_message(self, chat_id: int, message_id: int) -> Download | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT d.* FROM messages m JOIN downloads d ON d.id = m.download_id"
                " WHERE m.chat_id = ? AND m.message_id = ?",
                (chat_id, message_id),
            ).fetchone()
        return _row(row) if row else None

    def get(self, download_id: int, user_id: int) -> Download | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM downloads WHERE id = ? AND user_id = ?",
                (download_id, user_id),
            ).fetchone()
        return _row(row) if row else None

    def latest(self, user_id: int) -> Download | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return _row(row) if row else None

    def history(self, user_id: int, limit: int = 10) -> list[Download]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_row(r) for r in rows]

    def search(self, user_id: int, text: str, limit: int = 10) -> list[Download]:
        like = f"%{text}%"
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM downloads WHERE user_id = ? AND"
                " (title LIKE ? OR url LIKE ? OR note LIKE ? OR site LIKE ?)"
                " ORDER BY id DESC LIMIT ?",
                (user_id, like, like, like, like, limit),
            ).fetchall()
        return [_row(r) for r in rows]

    def set_note(self, download_id: int, user_id: int, note: str) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "UPDATE downloads SET note = ? WHERE id = ? AND user_id = ?",
                (note, download_id, user_id),
            )
        return cursor.rowcount > 0

    def delete(self, download_id: int, user_id: int) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "DELETE FROM downloads WHERE id = ? AND user_id = ?",
                (download_id, user_id),
            )
        return cursor.rowcount > 0
