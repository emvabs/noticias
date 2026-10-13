"""Fetching: one run at a time across processes, and per-source request headers."""
import fcntl

import httpx
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


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Uma notícia qualquer</title><link>https://x/1</link></item></channel></rss>""".encode()


def test_a_source_can_send_its_own_user_agent(lock_file, monkeypatch):
    seen = {}

    def answer(request):
        seen[str(request.url)] = request.headers["user-agent"]
        return httpx.Response(200, content=RSS)

    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))
    monkeypatch.setattr(ingest.config, "load_sources", lambda enabled_only=True: [
        {"id": "a", "name": "A", "feeds": ["https://a/feed"], "author_from": "none"},
        {"id": "b", "name": "B", "feeds": ["https://b/feed"], "author_from": "none",
         "user_agent": "Browser/1.0"}])
    ingest.fetch_all(db.connect(":memory:"))
    default = ingest.config.load_config()["fetch"]["user_agent"]
    assert seen == {"https://a/feed": default, "https://b/feed": "Browser/1.0"}
