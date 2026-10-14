"""GET /healthz, and status read from the fetch history any process writes."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, db, ingest, main

OBSERVADOR = "https://observador.pt/feed/"
BBC = "https://feeds.bbci.co.uk/news/world/rss.xml"


@pytest.fixture
def conn(monkeypatch):
    conn = db.connect(":memory:")
    monkeypatch.setattr(main, "conn", conn)
    real = config.load_config
    monkeypatch.setattr(config, "load_config",
                        lambda: {**real(), "fetch": {**real()["fetch"], "interval_minutes": 20}})
    return conn


@pytest.fixture
def client(conn):
    return TestClient(main.app)


def run(conn, minutes_ago, results, finished=True):
    """A fetch run as another process would record it: results = [(source, url, items)]."""
    when = ingest.iso(ingest.utcnow() - timedelta(minutes=minutes_ago))
    run_id = conn.execute("INSERT INTO fetch_runs (started_at, finished_at, ok) VALUES (?,?,?)",
                          (when, when if finished else None,
                           int(finished and any(n for _, _, n in results)))).lastrowid
    for source, url, items in results:
        conn.execute("INSERT INTO feed_results VALUES (?,?,?,?,?,?)",
                     (run_id, source, url, items, None if items else "feed vazio", when))
    conn.commit()


def test_no_fetch_yet_is_unhealthy(client):
    res = client.get("/healthz")
    assert res.status_code == 503
    assert res.json()["status"] == "stale" and res.json()["last_success"] is None


def test_a_recent_successful_fetch_is_healthy(conn, client):
    run(conn, 5, [("observador", OBSERVADOR, 30)])
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["max_age_minutes"] == 60
    assert body["minutes_since_success"] == pytest.approx(5, abs=0.2)
    assert body["running"] is False


def test_a_fetch_older_than_three_intervals_is_stale(conn, client):
    run(conn, 61, [("observador", OBSERVADOR, 30)])
    assert client.get("/healthz").status_code == 503


def test_a_run_where_every_feed_failed_is_not_a_success(conn, client):
    run(conn, 61, [("observador", OBSERVADOR, 30)])
    run(conn, 1, [("observador", OBSERVADOR, 0), ("bbc", BBC, 0)])
    body = client.get("/healthz").json()
    assert body["status"] == "stale"
    assert body["minutes_since_success"] == pytest.approx(61, abs=0.2)


def test_an_unfinished_run_is_not_a_success(conn, client):
    run(conn, 1, [("observador", OBSERVADOR, 30)], finished=False)
    assert client.get("/healthz").status_code == 503


def test_feeds_empty_on_every_run_of_the_last_day_are_listed(conn, client):
    run(conn, 60 * 30, [("observador", OBSERVADOR, 25), ("bbc", BBC, 0)])   # 30 h ago
    run(conn, 60, [("observador", OBSERVADOR, 0), ("bbc", BBC, 0)])
    run(conn, 5, [("observador", OBSERVADOR, 30), ("bbc", BBC, 0)])
    empty = client.get("/healthz").json()["empty_feeds_24h"]
    assert empty == [{"source": "bbc", "name": "BBC News", "url": BBC,
                      "runs": 2, "last_error": "feed vazio"}]


def test_feeds_no_longer_in_sources_yaml_are_not_listed(conn, client):
    run(conn, 5, [("observador", "https://observador.pt/old-feed", 0),
                  ("observador", OBSERVADOR, 10)])
    assert client.get("/healthz").json()["empty_feeds_24h"] == []


def test_status_shows_a_fetch_made_by_another_process(conn, client):
    assert client.get("/api/status").json()["last_updated"] is None
    run(conn, 3, [("observador", OBSERVADOR, 10)])
    last = conn.execute("SELECT finished_at FROM fetch_runs").fetchone()[0]
    assert client.get("/api/status").json()["last_updated"] == last
    assert client.get("/api/feed?positive_pct=0").json()["last_updated"] == last


def test_source_errors_come_from_the_latest_run(conn, client):
    run(conn, 30, [("observador", OBSERVADOR, 0), ("bbc", BBC, 0)])
    run(conn, 5, [("observador", OBSERVADOR, 12), ("bbc", BBC, 0)])
    errors = {s["id"]: s["errors"] for s in client.get("/api/sources").json()}
    assert errors["observador"] == []
    assert errors["bbc"] == [f"{BBC}: feed vazio"]


def test_history_older_than_a_week_is_pruned(conn):
    run(conn, 60 * 24 * 8, [("observador", OBSERVADOR, 10)])
    ingest.finish_run(conn, ingest.start_run(conn), ok=True)
    assert conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM feed_results").fetchone()[0] == 0
