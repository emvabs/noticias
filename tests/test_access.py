"""Shared password: who is asked, and what opens the site."""
import base64

import pytest
from fastapi.testclient import TestClient

from app import access, db, main


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    (tmp_path / "password.txt").write_text("segredo-do-grupo\n", encoding="utf-8")
    monkeypatch.setattr(access, "PASSWORD_FILE", tmp_path / "password.txt")
    monkeypatch.setattr(main, "conn", db.connect(":memory:"))
    return TestClient(main.app)


def test_no_file_means_open(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "conn", db.connect(":memory:"))
    assert TestClient(main.app).get("/api/status").status_code == 200


def test_empty_file_means_open(tmp_path, monkeypatch):
    (tmp_path / "p.txt").write_text("  \n")
    monkeypatch.setattr(access, "PASSWORD_FILE", tmp_path / "p.txt")
    assert access.password() is None


def test_outside_visit_is_asked(client):
    r = client.get("/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/api/feed").status_code == 401
    assert client.get("/static/app.js").status_code == 401
    assert client.post("/api/refresh").status_code == 401


def test_right_password_any_user_name(client):
    assert client.get("/api/status", headers={"Authorization": basic("ana", "segredo-do-grupo")}).status_code == 200
    assert client.get("/api/status", headers={"Authorization": basic("", "segredo-do-grupo")}).status_code == 200


@pytest.mark.parametrize("header", [
    basic("ana", "errada"), basic("ana", "segredo-do-grupo ").rstrip(), "Basic !!!", "Bearer segredo-do-grupo",
    "Basic " + base64.b64encode(b"segredo-do-grupo").decode(),  # no colon
])
def test_wrong_or_malformed_is_refused(client, header):
    assert client.get("/api/status", headers={"Authorization": header}).status_code == 401


def test_local_visit_needs_no_password():
    assert access.is_local("127.0.0.1", {"host": "localhost:8000"})
    assert access.is_local("::1", {"host": "[::1]:8000"})
    assert access.is_local("127.0.0.1", {"host": "127.0.0.1:8000"})


def test_funnel_is_not_local():
    # Tailscale Funnel connects from loopback but names the public host and adds proxy headers.
    assert not access.is_local("127.0.0.1", {"host": "mac.tail1234.ts.net", "x-forwarded-for": "203.0.113.9"})
    assert not access.is_local("127.0.0.1", {"host": "localhost:8000", "x-forwarded-for": "203.0.113.9"})
    assert not access.is_local("127.0.0.1", {"host": "mac.tail1234.ts.net"})
    assert not access.is_local("192.168.1.20", {"host": "localhost:8000"})
