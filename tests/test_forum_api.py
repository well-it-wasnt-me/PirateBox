"""Forum API tests: keeping the threads civil by brute force."""

from app import db


def test_forum_page_empty(client):
    """Empty forum should admit it's empty."""
    response = client.get("/forum")
    assert response.status_code == 200
    assert "No threads yet" in response.text


def test_create_thread_and_reply(client):
    """Create a thread, reply once, and confirm the count."""
    create = client.post(
        "/forum",
        data={"title": "Hello", "nickname": "Sam", "message": "First post"},
        follow_redirects=False,
    )
    assert create.status_code == 303

    thread_id = db.list_threads()[0].id

    thread_page = client.get(f"/forum/{thread_id}")
    assert thread_page.status_code == 200
    assert "First post" in thread_page.text

    reply = client.post(
        f"/forum/{thread_id}/reply",
        data={"nickname": "Alex", "message": "Reply"},
        follow_redirects=False,
    )
    assert reply.status_code == 303

    posts = db.list_posts(thread_id)
    assert len(posts) == 2


def test_create_thread_requires_fields(client):
    """Missing title or message should be rejected."""
    response = client.post("/forum", data={"title": "", "message": ""})
    assert response.status_code == 400
