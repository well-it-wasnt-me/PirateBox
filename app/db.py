"""SQLite persistence for PirateBox, because memory is cheap and regret is free."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR = Path(os.getenv("PIRATEBOX_DATA_DIR", "./data"))
DB_PATH = Path(os.getenv("PIRATEBOX_DB_PATH", DATA_DIR / "piratebox.db"))
FILES_DIR = Path(os.getenv("PIRATEBOX_FILES_DIR", DATA_DIR / "files"))
MAX_UPLOAD_MB = int(os.getenv("PIRATEBOX_MAX_UPLOAD_MB", "512"))

MAX_NICKNAME_LEN = int(os.getenv("PIRATEBOX_MAX_NICKNAME_LEN", "32"))
MAX_MESSAGE_LEN = int(os.getenv("PIRATEBOX_MAX_MESSAGE_LEN", "500"))
MAX_THREAD_TITLE_LEN = int(os.getenv("PIRATEBOX_MAX_THREAD_TITLE_LEN", "120"))


@dataclass(frozen=True)
class StoredFile:
    """Metadata for an uploaded file sitting on disk."""
    id: int
    original_name: str
    stored_name: str
    size_bytes: int
    sha256: str
    uploaded_at: str


@dataclass(frozen=True)
class ChatMessage:
    """A single chat message with a timestamp and no accountability."""
    id: int
    nickname: str
    message: str
    created_at: str


@dataclass(frozen=True)
class ForumThread:
    """Summary view of a forum thread, for when you want the highlights only."""
    id: int
    title: str
    nickname: str
    created_at: str
    post_count: int
    last_activity: Optional[str]


@dataclass(frozen=True)
class ForumPost:
    """A forum post that will live forever in SQLite, like it or not."""
    id: int
    thread_id: int
    nickname: str
    message: str
    created_at: str


def _utc_now() -> str:
    """Return a UTC timestamp without microseconds for predictable storage."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_storage() -> None:
    """Create storage directories so the app can pretend it's organized."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    """Open a SQLite connection with row dictionaries."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize SQLite tables if they do not exist."""
    ensure_storage()
    with _connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forum_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                nickname TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forum_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                nickname TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(thread_id) REFERENCES forum_threads(id) ON DELETE CASCADE
            );
            """
        )


def list_files(limit: int = 200) -> list[StoredFile]:
    """Return recent files, newest first, capped at `limit`."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, original_name, stored_name, size_bytes, sha256, uploaded_at
            FROM files
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [StoredFile(**row) for row in rows]


def get_file(file_id: int) -> Optional[StoredFile]:
    """Fetch a single file record by id."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, original_name, stored_name, size_bytes, sha256, uploaded_at
            FROM files
            WHERE id = ?
            """,
            (file_id,),
        ).fetchone()
    return StoredFile(**row) if row else None


def insert_file(original_name: str, stored_name: str, size_bytes: int, sha256: str) -> int:
    """Persist file metadata and return the new id."""
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO files (original_name, stored_name, size_bytes, sha256, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (original_name, stored_name, size_bytes, sha256, _utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_chat_messages(after_id: int = 0, limit: int = 200) -> list[ChatMessage]:
    """Return chat messages after a given id."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, nickname, message, created_at
            FROM chat_messages
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (after_id, limit),
        ).fetchall()
    return [ChatMessage(**row) for row in rows]


def insert_chat_message(nickname: str, message: str) -> ChatMessage:
    """Insert a chat message and return the stored row."""
    created_at = _utc_now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO chat_messages (nickname, message, created_at)
            VALUES (?, ?, ?)
            """,
            (nickname, message, created_at),
        )
        conn.commit()
        msg_id = int(cur.lastrowid)
    return ChatMessage(id=msg_id, nickname=nickname, message=message, created_at=created_at)


def list_threads(limit: int = 200) -> list[ForumThread]:
    """List threads with counts and last activity, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.nickname, t.created_at,
                   (SELECT COUNT(*) FROM forum_posts p WHERE p.thread_id = t.id) AS post_count,
                   (SELECT MAX(created_at) FROM forum_posts p WHERE p.thread_id = t.id) AS last_activity
            FROM forum_threads t
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [ForumThread(**row) for row in rows]


def get_thread(thread_id: int) -> Optional[ForumThread]:
    """Fetch a forum thread summary by id."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT t.id, t.title, t.nickname, t.created_at,
                   (SELECT COUNT(*) FROM forum_posts p WHERE p.thread_id = t.id) AS post_count,
                   (SELECT MAX(created_at) FROM forum_posts p WHERE p.thread_id = t.id) AS last_activity
            FROM forum_threads t
            WHERE t.id = ?
            """,
            (thread_id,),
        ).fetchone()
    return ForumThread(**row) if row else None


def list_posts(thread_id: int) -> list[ForumPost]:
    """List posts for a specific thread, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, thread_id, nickname, message, created_at
            FROM forum_posts
            WHERE thread_id = ?
            ORDER BY id ASC
            """,
            (thread_id,),
        ).fetchall()
    return [ForumPost(**row) for row in rows]


def create_thread(title: str, nickname: str, message: str) -> int:
    """Create a thread and its first post, then return the thread id."""
    created_at = _utc_now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO forum_threads (title, nickname, created_at)
            VALUES (?, ?, ?)
            """,
            (title, nickname, created_at),
        )
        thread_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO forum_posts (thread_id, nickname, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (thread_id, nickname, message, created_at),
        )
        conn.commit()
    return thread_id


def insert_post(thread_id: int, nickname: str, message: str) -> ForumPost:
    """Insert a reply into a thread and return the stored post."""
    created_at = _utc_now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO forum_posts (thread_id, nickname, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (thread_id, nickname, message, created_at),
        )
        conn.commit()
        post_id = int(cur.lastrowid)
    return ForumPost(
        id=post_id,
        thread_id=thread_id,
        nickname=nickname,
        message=message,
        created_at=created_at,
    )


def normalize_nickname(value: Optional[str]) -> str:
    """Normalize nicknames to something short and vaguely human."""
    if not value:
        return "Anonymous"
    clean = " ".join(value.strip().split())
    if not clean:
        return "Anonymous"
    return clean[:MAX_NICKNAME_LEN]


def normalize_message(value: Optional[str], *, max_len: int) -> str:
    """Trim and collapse whitespace, then enforce a max length."""
    if not value:
        return ""
    clean = " ".join(value.strip().split())
    return clean[:max_len]


def store_upload(file_obj, original_name: str) -> StoredFile:
    """Stream an upload to disk, hash it, and save metadata."""
    stored_name = uuid.uuid4().hex
    target_path = FILES_DIR / stored_name
    size_bytes = 0
    digest = hashlib.sha256()

    try:
        with target_path.open("wb") as target:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_MB * 1024 * 1024:
                    raise ValueError("File too large")
                digest.update(chunk)
                target.write(chunk)
    except Exception:
        if target_path.exists():
            target_path.unlink()
        raise

    file_id = insert_file(
        original_name=original_name,
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
    )

    return StoredFile(
        id=file_id,
        original_name=original_name,
        stored_name=stored_name,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
        uploaded_at=_utc_now(),
    )
