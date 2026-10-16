"""app/security.py: the dashboard has no login and relies on listening on 127.0.0.1, so it must also
refuse other websites in the same browser -- DNS rebinding, cross-site POSTs and framing."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import _host_name


@pytest.fixture()
def client():
    return TestClient(app)  # no lifespan: nothing here reaches the database


def test_host_names_are_compared_without_the_port():
    assert _host_name("localhost:8000") == "localhost"
    assert _host_name("[::1]:8000") == "::1"
    assert _host_name("127.0.0.1") == "127.0.0.1"


def test_an_unknown_host_is_refused(client):
    response = client.get("/api/health", headers={"Host": "evil.example:8000"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_local_hosts_are_served(client, host):
    assert client.get("/index.html", headers={"Host": host}).status_code == 200


def test_a_cross_site_post_is_refused_before_it_does_anything(client):
    response = client.post("/api/collect/run", headers={"Origin": "https://evil.example"}, json={})
    assert response.status_code == 403
    response = client.post("/api/collect/run", headers={"Sec-Fetch-Site": "cross-site"}, json={})
    assert response.status_code == 403


def test_a_same_origin_post_passes_the_check(client):
    # Validation fails further in (empty name), which proves the request got past the middleware.
    response = client.post("/api/projects", headers={"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"},
                           json={"name": ""})
    assert response.status_code == 422


def test_pages_cannot_be_framed(client):
    response = client.get("/index.html")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", ["/", "/styles.css", "/js/main.js", "/js/views/projects.js"])
def test_dashboard_files_revalidate_even_on_conditional_requests(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    cached = client.get(path, headers={"If-None-Match": response.headers["ETag"]})
    assert cached.status_code == 304
    assert cached.headers["Cache-Control"] == "no-cache"
