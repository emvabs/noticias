"""One fetch at a time, across threads and processes."""
import fcntl

import pytest

from app import db, ingest


@pytest.fixture
def lock_file(tmp_path, monkeypatch):
    path = tmp_path / "news.db.fetch.lock"
    monkeypatch.setattr(ingest, "lock_path", lambda: path)
    return path


def held_by_another_process(path):
    """flock is per open file, so a second open() stands in for another process."""
    f = open(path, "a")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def test_fetch_is_skipped_while_another_process_fetches(lock_file, monkeypatch):
    monkeypatch.setattr(ingest, "_fetch_all", lambda conn: pytest.fail("fetched twice"))
    with held_by_another_process(lock_file):
        assert ingest.fetch_all() == {"skipped": True, "reason": "already running"}
        assert ingest.is_running()
    assert not ingest.is_running()


def test_lock_is_released_after_a_run_even_if_it_fails(lock_file, monkeypatch):
    def boom(conn):
        assert ingest.is_running()
        raise RuntimeError("boom")
    monkeypatch.setattr(ingest, "_fetch_all", boom)
    with pytest.raises(RuntimeError):
        ingest.fetch_all()
    assert not ingest.is_running()
    assert not ingest.status["running"]


def test_fetch_runs_when_nobody_else_is_fetching(lock_file, monkeypatch):
    monkeypatch.setattr(ingest, "_fetch_all", lambda conn: {"ran": True})
    assert ingest.fetch_all(db.connect(":memory:")) == {"ran": True}
