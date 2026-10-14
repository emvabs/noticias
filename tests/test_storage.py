from datetime import timedelta

from app import db, ingest



def article(url, title, age_days=0):
    when = ingest.iso(ingest.utcnow() - timedelta(days=age_days))
    return {"source": "x", "title": title, "title_norm": ingest.normalize_title(title),
            "summary": "", "url": url, "published_at": when, "fetched_at": when}


def test_dedupe_by_url_and_normalized_title():
    conn = db.connect(":memory:")
    arts = [article("https://a/1", "Guerra na Europa"),
            article("https://a/1", "Outro título, mesmo URL"),
            article("https://a/2", "guerra na Europa!"),       # same story, new URL
            article("https://a/3", "Notícia diferente")]
    assert ingest.store(conn, arts) == 2


def test_cleanup_removes_articles_older_than_retention():
    conn = db.connect(":memory:")
    ingest.store(conn, [article("https://a/1", "Nova", 1), article("https://a/2", "Velha", 8)])
    assert ingest.cleanup(conn, 7) == 1
    assert [r[0] for r in conn.execute("SELECT title FROM articles")] == ["Nova"]


def test_migration_resyncs_scope_and_language_from_sources_yaml():
    """Editing sources.yaml applies to stored articles on the next start."""
    conn = db.connect(":memory:")
    ingest.store(conn, [article("https://bbc/1", "Old")])          # stored as portugal/pt
    conn.execute("UPDATE articles SET source = 'bbc'")
    db.migrate(conn)
    row = conn.execute("SELECT scope, language FROM articles").fetchone()
    assert (row["scope"], row["language"]) == ("world", "en")


def test_articles_are_stored_with_scope_and_language():
    conn = db.connect(":memory:")
    art = article("https://bbc/1", "Air strike kills 12 civilians")
    art.update(source="bbc", scope="world", language="en")
    ingest.store(conn, [art])
    row = conn.execute("SELECT scope, language, label FROM articles").fetchone()
    assert (row["scope"], row["language"], row["label"]) == ("world", "en", "negative")
