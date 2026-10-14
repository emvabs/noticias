"""Shared test setup."""
import pytest

from app import access


@pytest.fixture(autouse=True)
def no_password(monkeypatch, tmp_path):
    """Tests never read the real data/password.txt; test_access sets its own."""
    monkeypatch.setattr(access, "PASSWORD_FILE", tmp_path / "no-password.txt")
