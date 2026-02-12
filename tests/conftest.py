"""Pytest fixtures for PirateBox, the only place we're optimistic on purpose."""

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app as fastapi_app


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    """Point the DB and files dir at tmp space so tests can break things safely."""
    db_path = tmp_path / "piratebox.db"
    files_dir = tmp_path / "files"

    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "FILES_DIR", files_dir)
    monkeypatch.setattr(db, "MAX_UPLOAD_MB", 1)

    db.init_db()
    return tmp_path


@pytest.fixture()
def client(storage):
    """Provide a test client wired to the FastAPI app."""
    with TestClient(fastapi_app) as test_client:
        yield test_client


@pytest.fixture()
def sample_file():
    """Return a small file payload for upload tests."""
    content = b"hello piratebox"
    return ("hello.txt", io.BytesIO(content), "text/plain"), content
