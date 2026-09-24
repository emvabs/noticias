"""The independent group: filtering, retention, ingest window and topics."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, db, ingest, main, topics


def article(title, source, group, days_old=0, scope="portugal"):
    when = ingest.iso(ingest.utcnow() - timedelta(days=days_old))
    return {"source": source, "title": title, "title_norm": ingest.normalize_title(title),
            "summary": "", "url": f"https://x/{source}/{abs(hash(title))}", "scope": scope,
            "language": "pt", "group": group, "published_at": when, "fetched_at": when}


@pytest.fixture
def client(monkeypatch):
    conn = db.connect(":memory:")
    ingest.store(conn, [
        article("Governo aprova orçamento", "publico", "mainstream"),
        article("Ministro fala ao país", "rtp", "mainstream"),
        article("Investigação sobre policiamento em bairros", "fumaca", "independent", days_old=20),
        article("Reportagem sobre habitação", "divergente", "independent", days_old=40),
        article("Crónica sobre escolas", "shifter", "independent", days_old=1),
    ])
    monkeypatch.setattr(main, "conn", conn)
    return TestClient(main.app)


MIX = "positive_pct=0&limit=50"      # these fixtures are about groups, not the mix


def sources_of(response):
    return sorted({a["source"] for a in response.json()["articles"]})


def test_feed_defaults_to_mainstream(client):
    assert sources_of(client.get(f"/api/feed?{MIX}")) == ["publico", "rtp"]


def test_feed_can_show_only_the_independent_group(client):
    assert sources_of(client.get(f"/api/feed?group=independent&{MIX}")) == \
        ["divergente", "fumaca", "shifter"]


def test_group_filter_applies_before_topics_and_the_mix(client):
    data = client.get(f"/api/feed?group=independent&{MIX}").json()
    assert data["group"] == "independent"
    assert all(a["group"] == "independent" for a in data["articles"])


def test_sources_endpoint_reports_the_group_and_last_article(client):
    rows = client.get("/api/sources?scope=portugal&group=independent").json()
    assert {r["id"] for r in rows} >= {"fumaca", "divergente", "shifter"}
    assert all(r["group"] == "independent" for r in rows)
    assert any(r["last_published"] for r in rows)


def test_topics_are_computed_per_group(client):
    topics.recompute_all(main.conn)
    independent = [t["slug"] for t in client.get("/api/topics?group=independent").json()]
    mainstream = [t["slug"] for t in client.get("/api/topics?group=mainstream").json()]
    assert set(independent).isdisjoint(mainstream)


def test_independent_topics_use_the_wider_window_and_lower_threshold():
    """48 hours and 3 articles would find nothing in a group that posts weekly."""
    hours, _, max_topics, min_articles = topics.settings_for("independent")
    assert hours >= 336 and min_articles <= 2 and 6 <= max_topics <= 8
    assert topics.settings_for("mainstream") == (
        topics.WINDOW_HOURS, topics.BACKGROUND_DAYS, topics.MAX_TOPICS, topics.MIN_ARTICLES)


def test_retention_keeps_independent_pieces_longer():
    conn = db.connect(":memory:")
    ingest.store(conn, [
        article("Notícia generalista antiga", "publico", "mainstream", days_old=20),
        article("Reportagem independente antiga", "fumaca", "independent", days_old=20),
    ])
    ingest.cleanup(conn, retention_days=14)
    left = [r[0] for r in conn.execute("SELECT source FROM articles")]
    assert left == ["fumaca"]          # kept by groups.independent.retention_days


def test_independent_retention_still_has_a_limit():
    conn = db.connect(":memory:")
    days = config.group_settings("independent", "retention_days")
    ingest.store(conn, [article("Peça muito antiga", "fumaca", "independent", days_old=days + 5)])
    ingest.cleanup(conn, retention_days=14)
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_feed_boilerplate_is_stripped():
    """WordPress footers would otherwise become topics and tint the score."""
    assert ingest.clean_text(
        "Texto da peça. O conteúdo Título apareceu primeiro em Fumaça.") == "Texto da peça."
    assert ingest.clean_text(
        "Real text here. The post Something appeared first on Shifter.") == "Real text here."


def test_summaries_are_truncated_to_the_configured_length():
    limit = ingest.summary_limit()
    assert limit <= 300
    assert len(ingest.clean_text("palavra " * 200)) <= limit + 1   # +1 for the ellipsis


def test_world_scope_has_no_independent_outlets(client):
    """The five are Portuguese: asking for them in Mundo must not be a silent blank."""
    assert client.get("/api/sources?scope=world&group=independent").json() == []
    assert client.get(f"/api/feed?scope=world&group=independent&{MIX}").json()["count"] == 0
