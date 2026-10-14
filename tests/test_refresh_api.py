"""POST /api/refresh: localhost, or a request carrying NOTICIAS_ADMIN_TOKEN."""
import pytest
from fastapi.testclient import TestClient

from app import ingest, main

TOKEN = "s3cret-token"


@pytest.fixture(autouse=True)
def no_real_fetch(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_all", lambda: {"sources": {}, "total": 0})
    monkeypatch.delenv("NOTICIAS_ADMIN_TOKEN", raising=False)


def client(host):
    return TestClient(main.app, client=(host, 50000), base_url="http://localhost:8000")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"])
def test_localhost_may_refresh_without_a_token(host):
    assert client(host).post("/api/refresh").status_code == 200


def test_other_hosts_are_refused():
    assert client("203.0.113.9").post("/api/refresh").status_code == 403


def test_refused_requests_do_not_fetch(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_all", lambda: pytest.fail("fetched"))
    client("203.0.113.9").post("/api/refresh")


def test_a_request_forwarded_by_a_local_proxy_is_not_local():
    """Behind nginx on the same machine every request arrives from 127.0.0.1."""
    for header in ("X-Forwarded-For", "Forwarded", "X-Real-IP"):
        res = client("127.0.0.1").post("/api/refresh", headers={header: "203.0.113.9"})
        assert res.status_code == 403, header


def test_the_admin_token_allows_any_host(monkeypatch):
    monkeypatch.setenv("NOTICIAS_ADMIN_TOKEN", TOKEN)
    res = client("203.0.113.9").post("/api/refresh", headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200


@pytest.mark.parametrize("header", [f"Bearer {TOKEN}x", "Bearer ", f"Basic {TOKEN}", TOKEN])
def test_a_wrong_token_is_refused(monkeypatch, header):
    monkeypatch.setenv("NOTICIAS_ADMIN_TOKEN", TOKEN)
    res = client("203.0.113.9").post("/api/refresh", headers={"Authorization": header})
    assert res.status_code == 403


def test_with_no_token_configured_no_token_works():
    """An empty Bearer must not match an empty (unset) token."""
    res = client("203.0.113.9").post("/api/refresh", headers={"Authorization": "Bearer "})
    assert res.status_code == 403


def test_the_token_is_compared_in_constant_time(monkeypatch):
    seen = []
    real = main.hmac.compare_digest
    monkeypatch.setattr(main.hmac, "compare_digest", lambda a, b: seen.append(1) or real(a, b))
    monkeypatch.setenv("NOTICIAS_ADMIN_TOKEN", TOKEN)
    client("203.0.113.9").post("/api/refresh", headers={"Authorization": f"Bearer {TOKEN}"})
    assert seen
