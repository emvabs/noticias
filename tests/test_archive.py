"""The article archive and owner mentions."""
import json
from datetime import timedelta

from app import archive, db, ingest, ownership


def article(n, title, age_days=0, source="publico", authors=None, summary=""):
    when = ingest.iso(ingest.utcnow() - timedelta(days=age_days))
    return {"source": source, "title": title, "title_norm": ingest.normalize_title(title),
            "summary": summary, "url": f"https://x/{n}", "published_at": when,
            "fetched_at": when, "authors": authors}


def archived(conn):
    return {r["url"]: dict(r) for r in conn.execute("SELECT * FROM article_archive")}


def test_archive_keeps_articles_that_retention_deletes():
    conn = db.connect(":memory:")
    ingest.store(conn, [article(1, "Nova notícia", 1), article(2, "Notícia velha", 30)])
    archive.sync(conn)
    assert ingest.cleanup(conn, 14) == 1
    archive.sync(conn)
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    assert set(archived(conn)) == {"https://x/1", "https://x/2"}


def test_archive_follows_rescoring_and_late_bylines():
    conn = db.connect(":memory:")
    ingest.store(conn, [article(1, "Uma notícia", authors=None)])
    archive.sync(conn)
    conn.execute("UPDATE articles SET label = 'negative', authors = '[\"Ana Sousa\"]'")
    archive.sync(conn)
    row = archived(conn)["https://x/1"]
    assert (row["label"], row["authors"]) == ("negative", '["Ana Sousa"]')


def test_archive_accumulates_topics():
    """articles.topics only holds what is trending now; the archive remembers."""
    conn = db.connect(":memory:")
    ingest.store(conn, [article(1, "Uma notícia")])
    for topics in ('["irao"]', '[]', '["eua"]'):
        conn.execute("UPDATE articles SET topics = ?", (topics,))
        archive.sync(conn)
    assert json.loads(archived(conn)["https://x/1"]["topics"]) == ["irao", "eua"]


def test_archive_remembers_topic_labels():
    conn = db.connect(":memory:")
    conn.execute("""INSERT INTO topics_cache (scope, "group", slug, label, count, rank, computed_at)
                    VALUES ('portugal', 'mainstream', 'irao', 'Irão', 5, 0, 'x')""")
    archive.sync(conn)
    conn.execute("DELETE FROM topics_cache")
    archive.sync(conn)
    assert archive.labels(conn, "portugal") == {"irao": "Irão"}


def test_mentions_are_whole_words_with_exact_case():
    text = ingest.raw_text("A Sonae e o Ministério Público", "Sonaecom vende; SIC e sic")
    assert ownership.mentions(text, ["Sonae", "Sonaecom", "SIC", "Belmiro de Azevedo"]) == \
        ["Sonae", "Sonaecom", "SIC"]
    assert ownership.mentions(ingest.raw_text("A empresa Sonaecom", ""), ["Sonae"]) == []
    assert ownership.mentions(ingest.raw_text("sic transit", ""), ["SIC"]) == []
    assert ownership.mentions(ingest.raw_text("Belmiro de Azevedo morreu", ""),
                              ["Belmiro de Azevedo"]) == ["Belmiro de Azevedo"]


def test_archive_stores_owner_mentions(monkeypatch):
    monkeypatch.setattr(ownership, "keywords_by_source", lambda: {"publico": ["Sonae"]})
    conn = db.connect(":memory:")
    ingest.store(conn, [article(1, "Sonae compra empresa"), article(2, "Sonae", source="expresso"),
                        article(3, "Outra coisa")])
    archive.sync(conn)
    rows = archived(conn)
    assert json.loads(rows["https://x/1"]["owner_mentions"]) == ["Sonae"]
    assert json.loads(rows["https://x/2"]["owner_mentions"]) == []   # not Expresso's owner
    assert json.loads(rows["https://x/3"]["owner_mentions"]) == []


def test_fetch_cycle_archives_before_cleanup(monkeypatch):
    """fetch_all must copy articles into the archive before retention deletes them."""
    conn = db.connect(":memory:")
    ingest.store(conn, [article(1, "Notícia velha", 30)])
    conn.execute("DELETE FROM article_archive")      # as if it was never archived
    monkeypatch.setattr(ingest.config, "load_sources", lambda enabled_only=True: [])
    ingest.fetch_all(conn)
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
    assert "https://x/1" in archived(conn)


def test_feed_flags_articles_that_mention_their_owner(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setattr(ownership, "keywords_by_source", lambda: {"publico": ["Sonae"]})
    conn = db.connect(":memory:")
    ingest.store(conn, [article(1, "Sonae vende participação"), article(2, "Chuva no norte")])
    monkeypatch.setattr(main, "conn", conn)
    got = TestClient(main.app).get("/api/feed?positive_pct=0&group=").json()["articles"]
    assert {a["title"]: a["owner_mentions"] for a in got} == {
        "Sonae vende participação": ["Sonae"], "Chuva no norte": []}
    sources = TestClient(main.app).get("/api/sources").json()
    assert next(s for s in sources if s["id"] == "publico")["owners"] == ["Sonae"]
