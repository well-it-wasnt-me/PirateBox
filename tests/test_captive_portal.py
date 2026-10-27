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


def test_captive_ack_silences_probes_without_cookie(client):
    """Phones probe without browser cookies, so the ack has to stick to the client IP too."""
    ack = client.get("/captive/ack")
    assert ack.status_code == 200
    client.cookies.clear()

    probe = client.get("/generate_204", follow_redirects=False)
    assert probe.status_code == 204


def test_apple_probe_gets_the_exact_page_it_wants(client):
    """iOS is picky; give it the canonical success page and nothing creative."""
    client.get("/captive/ack")
    response = client.get("/hotspot-detect.html")
    assert response.status_code == 200
    assert "<TITLE>Success</TITLE>" in response.text


def test_safe_next_blocks_open_redirects():
    """Backslashes and control characters are just redirects in a trench coat."""
    from app.main import _safe_next

    assert _safe_next("/files") == "/files"
    assert _safe_next(None) == "/"
    assert _safe_next("//evil.example") == "/"
    assert _safe_next("/\\evil.example") == "/"
    assert _safe_next("/\t/evil.example") == "/"
    assert _safe_next("https://evil.example") == "/"


def test_captive_ack_escapes_next_for_javascript(client):
    """The redirect target lands in a script tag as JSON, not as HTML soup."""
    response = client.get("/captive/ack", params={"next": "/forum?a=1&b=2"})
    assert response.status_code == 200
    assert 'const target = "/forum?a=1\\u0026b=2";' in response.text
