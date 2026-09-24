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


def test_migration_adds_scope_and_language_to_old_rows():
    """An existing v1 database (no scope/language) is upgraded and backfilled."""
    import sqlite3

    from app import db as dbmod

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, title TEXT NOT NULL,
        title_norm TEXT NOT NULL UNIQUE, summary TEXT NOT NULL DEFAULT '', url TEXT NOT NULL UNIQUE,
        published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, score REAL, label TEXT, matched_words TEXT)""")
    conn.execute("""INSERT INTO articles (source, title, title_norm, url, published_at, fetched_at)
                    VALUES ('publico', 'Velha', 'velha', 'https://a/1', '2026-09-21T10:00:00Z', '2026-09-21T10:00:00Z')""")
    conn.execute("""INSERT INTO articles (source, title, title_norm, url, published_at, fetched_at)
                    VALUES ('bbc', 'Old', 'old', 'https://a/2', '2026-09-21T10:00:00Z', '2026-09-21T10:00:00Z')""")
    dbmod.migrate(conn)

    rows = {r["source"]: (r["scope"], r["language"]) for r in conn.execute("SELECT * FROM articles")}
    assert rows == {"publico": ("portugal", "pt"), "bbc": ("world", "en")}


def test_articles_are_stored_with_scope_and_language():
    conn = db.connect(":memory:")
    art = article("https://bbc/1", "Air strike kills 12 civilians")
    art.update(source="bbc", scope="world", language="en")
    ingest.store(conn, [art])
    row = conn.execute("SELECT scope, language, label FROM articles").fetchone()
    assert (row["scope"], row["language"], row["label"]) == ("world", "en", "negative")
