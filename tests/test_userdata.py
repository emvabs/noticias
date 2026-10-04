"""Saved articles and muted words: kept in SQLite, applied before the mix."""
import pytest
from fastapi.testclient import TestClient

from app import db, ingest, main, userdata


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    now = ingest.utcnow()
    for i, (title, source) in enumerate([
            ("Benfica vence Sporting no dérbi", "rtp"),
            ("Futebol: FC Porto empata em casa", "publico"),
            ("Greve dos professores fecha escolas", "publico"),
            ("Incêndio em Leiria obriga a evacuar aldeias", "rtp"),
            ("IA chega às escolas portuguesas", "expresso")]):
        ingest.store(conn, [{
            "source": source, "title": title, "title_norm": ingest.normalize_title(title),
            "summary": "", "url": f"https://x/{i}", "scope": "portugal", "language": "pt",
            "published_at": ingest.iso(now), "fetched_at": ingest.iso(now)}])
    return conn


@pytest.fixture
def client(conn, monkeypatch):
    monkeypatch.setattr(main, "conn", conn)
    return TestClient(main.app)


def titles(data):
    return [a["title"] for a in data["articles"]]


ALL = "positive_pct=0&limit=50"


def test_saved_copy_survives_the_feed_cleanup(conn):
    first = conn.execute("SELECT id, url FROM articles ORDER BY id").fetchone()
    userdata.save(conn, first["id"])
    userdata.save(conn, first["id"])                          # twice: still one
    conn.execute("DELETE FROM articles")                      # retention ran
    kept = userdata.saved(conn)
    assert len(kept) == 1 and kept[0]["url"] == first["url"] and kept[0]["title"]
    userdata.unsave(conn, first["url"])
    assert userdata.saved(conn) == []


def test_saved_api(client, conn):
    article = conn.execute("SELECT id, url FROM articles ORDER BY id").fetchone()
    assert client.post(f"/api/saved/{article['id']}").status_code == 200
    assert client.post("/api/saved/999999").status_code == 404
    items = client.get("/api/saved").json()
    assert items[0]["url"] == article["url"] and items[0]["source_name"]
    client.delete("/api/saved", params={"url": article["url"]})
    assert client.get("/api/saved").json() == []


def test_muted_words_hide_articles_and_say_how_many(client):
    before = client.get(f"/api/feed?{ALL}").json()
    assert client.post("/api/muted", params={"term": "  futebol  "}).json() == {"term": "futebol"}
    after = client.get(f"/api/feed?{ALL}").json()
    assert "Futebol: FC Porto empata em casa" not in titles(after)
    assert len(titles(after)) == len(titles(before)) - 1
    assert after["muted"] == ["futebol"] and after["muted_hidden"] == 1
    client.delete("/api/muted", params={"term": "futebol"})
    assert client.get("/api/feed?" + ALL).json()["muted_hidden"] == 0


def test_muting_matches_whole_words_and_acronyms(client):
    client.post("/api/muted", params={"term": "IA"})
    found = titles(client.get(f"/api/feed?{ALL}").json())
    assert "IA chega às escolas portuguesas" not in found
    # "IA" is an acronym: it does not hide words that merely contain the letters
    assert any("Leiria" in t for t in found)


def test_muting_applies_before_the_mix(client, conn):
    """With every positive article muted, 100% positive is an empty feed, never padded."""
    conn.execute("UPDATE articles SET label = 'positive' WHERE title LIKE 'Benfica%'")
    client.post("/api/muted", params={"term": "benfica"})
    data = client.get("/api/feed?positive_pct=100&limit=10").json()
    assert data["articles"] == [] and data["limited_by"] == "positive"


def test_a_blank_or_huge_term_is_refused(client):
    assert client.post("/api/muted", params={"term": "   "}).status_code == 422
    assert client.post("/api/muted", params={"term": "x" * 200}).status_code == 422


def test_briefing_one_item_per_story_widest_first_and_honours_the_slider(client, conn):
    ids = [r[0] for r in conn.execute("SELECT id FROM articles ORDER BY id")]
    # two of the articles become one story
    conn.execute("UPDATE articles SET cluster_id = ? WHERE id IN (?, ?)", (ids[2], ids[2], ids[1]))
    data = client.get(f"/api/briefing?{ALL}").json()
    assert data["window_hours"] == 24
    assert data["articles"][0]["outlets"] >= data["articles"][-1]["outlets"]
    assert len(data["articles"]) == len({a.get("cluster_id") or a["id"] for a in data["articles"]})
    conn.execute("UPDATE articles SET label = 'negative'")
    assert client.get("/api/briefing?positive_pct=100").json()["articles"] == []
