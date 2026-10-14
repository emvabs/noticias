"""Shared test setup."""
import pytest

from app import access, ingest


@pytest.fixture(autouse=True)
def no_password(monkeypatch, tmp_path):
    """Tests never read the real data/password.txt; test_access sets its own."""
    monkeypatch.setattr(access, "PASSWORD_FILE", tmp_path / "no-password.txt")


@pytest.fixture(autouse=True)
def lock_file(monkeypatch, tmp_path):
    """Tests fetch under their own lock, so a running server's fetch cannot skip theirs."""
    path = tmp_path / "news.db.fetch.lock"
    monkeypatch.setattr(ingest, "lock_path", lambda: path)
    return path
