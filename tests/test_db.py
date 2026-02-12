"""Database tests: because trusting SQLite blindly is how regret starts."""

import hashlib
import io

import pytest

from app import db


def test_normalize_nickname_defaults():
    """Missing nicknames fall back to Anonymous."""
    assert db.normalize_nickname(None) == "Anonymous"
    assert db.normalize_nickname("") == "Anonymous"
    assert db.normalize_nickname("   ") == "Anonymous"


def test_normalize_nickname_trim_and_limit():
    """Nicknames get trimmed and capped to the configured length."""
    long_name = "x" * (db.MAX_NICKNAME_LEN + 5)
    assert db.normalize_nickname("  Jane   Doe ") == "Jane Doe"
    assert len(db.normalize_nickname(long_name)) == db.MAX_NICKNAME_LEN


def test_normalize_message():
    """Messages normalize whitespace and respect the max length."""
    assert db.normalize_message(None, max_len=10) == ""
    assert db.normalize_message("  hello   world ", max_len=20) == "hello world"
    assert db.normalize_message("abcdef", max_len=3) == "abc"


def test_store_upload_and_list_files(storage):
    """Uploads should land on disk and appear in listings."""
    content = b"piratebox data"
    file_obj = io.BytesIO(content)

    record = db.store_upload(file_obj, "data.txt")
    assert record.original_name == "data.txt"
    assert record.size_bytes == len(content)

    files = db.list_files()
    assert len(files) == 1
    fetched = files[0]
    assert fetched.id == record.id

    file_path = db.FILES_DIR / record.stored_name
    assert file_path.exists()
    digest = hashlib.sha256(content).hexdigest()
    assert record.sha256 == digest


def test_store_upload_too_large(storage, monkeypatch):
    """Oversized uploads get rejected and cleaned up."""
    monkeypatch.setattr(db, "MAX_UPLOAD_MB", 0)
    with pytest.raises(ValueError):
        db.store_upload(io.BytesIO(b"x"), "tiny.txt")

    assert db.list_files() == []
    assert list(db.FILES_DIR.glob("*")) == []


def test_chat_messages(storage):
    """Chat inserts should round-trip with ordering intact."""
    msg1 = db.insert_chat_message("Alpha", "hello")
    msg2 = db.insert_chat_message("Beta", "world")

    all_msgs = db.list_chat_messages(after_id=0)
    assert [m.id for m in all_msgs] == [msg1.id, msg2.id]

    later = db.list_chat_messages(after_id=msg1.id)
    assert [m.id for m in later] == [msg2.id]


def test_forum_threads_and_posts(storage):
    """Threads and replies should be persisted in order."""
    thread_id = db.create_thread("Test thread", "Sam", "first")

    threads = db.list_threads()
    assert len(threads) == 1
    assert threads[0].id == thread_id
    assert threads[0].post_count == 1

    posts = db.list_posts(thread_id)
    assert len(posts) == 1
    assert posts[0].message == "first"

    reply = db.insert_post(thread_id, "Alex", "reply")
    assert reply.thread_id == thread_id

    posts = db.list_posts(thread_id)
    assert len(posts) == 2
