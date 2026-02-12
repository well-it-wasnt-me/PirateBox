"""Chat API tests: proof the shouting still works when nobody listens."""

def test_chat_page(client):
    """Chat page loads and name-drops the shoutbox."""
    response = client.get("/chat")
    assert response.status_code == 200
    assert "Shoutbox" in response.text


def test_chat_requires_message(client):
    """Empty messages get bounced like they deserve."""
    response = client.post("/api/chat/messages", data={"nickname": "Test", "message": ""})
    assert response.status_code == 400


def test_chat_post_and_fetch(client):
    """Posting a message should show up in the fetch call."""
    post = client.post("/api/chat/messages", data={"nickname": "Test", "message": "hello"})
    assert post.status_code == 200
    payload = post.json()
    assert payload["message"]["message"] == "hello"

    fetch = client.get("/api/chat/messages?after_id=0")
    assert fetch.status_code == 200
    messages = fetch.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["message"] == "hello"

    after = client.get(f"/api/chat/messages?after_id={messages[0]['id']}")
    assert after.status_code == 200
    assert after.json()["messages"] == []
