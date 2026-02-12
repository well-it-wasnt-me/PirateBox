"""File API tests: upload, download, repeat until storage fills."""

from app import db


def test_files_page_empty(client):
    """Empty files page should say so."""
    response = client.get("/files")
    assert response.status_code == 200
    assert "No files yet" in response.text


def test_upload_and_download_file(client, sample_file):
    """Uploaded files should be retrievable byte-for-byte."""
    file_tuple, content = sample_file

    response = client.post("/files/upload", files={"file": file_tuple}, follow_redirects=False)
    assert response.status_code == 303

    files = db.list_files()
    assert len(files) == 1
    file_id = files[0].id

    download = client.get(f"/files/{file_id}/download")
    assert download.status_code == 200
    assert download.content == content


def test_download_missing_file(client):
    """Missing file ids should return 404."""
    response = client.get("/files/999/download")
    assert response.status_code == 404
