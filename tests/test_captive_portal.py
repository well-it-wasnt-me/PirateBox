"""Captive portal tests: because devices keep asking for the internet anyway."""

def test_captive_portal_redirect(client):
    """Connectivity probes should get redirected to the portal."""
    response = client.get("/generate_204", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/captive"


def test_unknown_path(client):
    """Unknown paths should stay unknown."""
    response = client.get("/definitely-missing")
    assert response.status_code == 404
    assert response.text == "Not found"
