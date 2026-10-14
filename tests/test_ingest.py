"""Fetching: one run at a time across processes, and per-source request headers."""
import fcntl

import httpx
import pytest

from app import db, ingest

# What config.load_sources() fills in, with bylines off (the test feed has none).
DEFAULTS = {**ingest.config.SOURCE_DEFAULTS, "author_from": "none"}


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
        {**DEFAULTS, "id": "a", "name": "A", "feeds": ["https://a/feed"]},
        {**DEFAULTS, "id": "b", "name": "B", "feeds": ["https://b/feed"],
         "user_agent": "Browser/1.0"}])
    ingest.fetch_all(db.connect(":memory:"))
    default = ingest.config.load_config()["fetch"]["user_agent"]
    assert seen == {"https://a/feed": default, "https://b/feed": "Browser/1.0"}


def test_each_run_and_feed_result_is_recorded(lock_file, monkeypatch):
    def answer(request):
        if request.url.host == "broken":
            return httpx.Response(500)
        return httpx.Response(200, content=RSS)

    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))
    monkeypatch.setattr(ingest.config, "load_sources", lambda enabled_only=True: [
        {**DEFAULTS, "id": "a", "name": "A", "feeds": ["https://a/feed", "https://broken/feed"]}])
    conn = db.connect(":memory:")
    result = ingest.fetch_all(conn)
    runs = conn.execute("SELECT * FROM fetch_runs").fetchall()
    assert len(runs) == 1 and runs[0]["ok"] == 1
    assert runs[0]["finished_at"] == result["finished_at"]
    feeds = {r["url"]: (r["items"], r["error"]) for r in conn.execute("SELECT * FROM feed_results")}
    assert feeds["https://a/feed"] == (1, None)
    assert feeds["https://broken/feed"][0] == 0
    assert feeds["https://broken/feed"][1].startswith("HTTPStatusError")
    assert ingest.last_finished(conn, ok_only=True) == result["finished_at"]


def test_rescore_runs_only_when_the_lexicons_change(monkeypatch):
    conn = db.connect(":memory:")
    ingest.store(conn, [{"source": "publico", "title": "Vitória histórica", "title_norm": "vitoria historica",
                         "summary": "", "url": "https://x/1", "published_at": "2026-10-01T10:00:00Z",
                         "fetched_at": "2026-10-01T10:00:00Z"}])
    version = ["v1"]
    monkeypatch.setattr(ingest.sentiment, "version", lambda: version[0])
    monkeypatch.setattr(ingest, "_rescored_with", None)
    assert ingest.rescore_all(conn) == 1
    assert ingest.rescore_all(conn) == 0          # nothing changed
    version[0] = "v2"                              # a word list was edited
    assert ingest.rescore_all(conn) == 1
