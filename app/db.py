"""SQLite storage."""
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    title_norm    TEXT NOT NULL UNIQUE,   -- dedupe of the same story posted twice
    summary       TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL UNIQUE,
    published_at  TEXT NOT NULL,          -- ISO-8601 UTC, e.g. 2026-09-21T16:23:09Z
    fetched_at    TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'portugal',  -- portugal / world
    "group"       TEXT NOT NULL DEFAULT 'mainstream', -- mainstream / independent
    language      TEXT NOT NULL DEFAULT 'pt',        -- escolhe o léxico
    score         REAL,
    label         TEXT,                   -- positive / neutral / negative
    matched_words TEXT,                   -- JSON list, for debugging
    topics        TEXT NOT NULL DEFAULT '[]', -- JSON list of topic slugs
    search_text   TEXT NOT NULL DEFAULT '',  -- title + summary, lowercase, no accents
    raw_text      TEXT NOT NULL DEFAULT ''   -- same text with capitals/accents (acronyms)
);

CREATE TABLE IF NOT EXISTS topics_cache (   -- current top topics per scope and group
    scope       TEXT NOT NULL,
    "group"     TEXT NOT NULL DEFAULT 'mainstream',
    slug        TEXT NOT NULL,
    label       TEXT NOT NULL,
    count       INTEGER NOT NULL,
    rank        INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (scope, "group", slug)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_label ON articles(label, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_scope ON articles(scope, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_group ON articles(scope, "group", published_at DESC);
"""


def migrate(conn):
    """Add columns introduced after the first version, backfilling from sources.yaml."""
    # topics_cache gained a "group" column; it is a cache, so rebuilding beats migrating
    cached = {row["name"] for row in conn.execute("PRAGMA table_info(topics_cache)")}
    if cached and "group" not in cached:
        conn.execute("DROP TABLE topics_cache")
        conn.executescript(SCHEMA)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(articles)")}
    added = []
    for column, declaration in (("scope", "TEXT NOT NULL DEFAULT 'portugal'"),
                                ("language", "TEXT NOT NULL DEFAULT 'pt'"),
                                ("topics", "TEXT NOT NULL DEFAULT '[]'"),
                                ("search_text", "TEXT NOT NULL DEFAULT ''"),
                                ("raw_text", "TEXT NOT NULL DEFAULT ''"),
                                ('"group"', "TEXT NOT NULL DEFAULT 'mainstream'")):
        if column.strip('"') not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {column} {declaration}")
            added.append(column)
    # Always keep scope/language/group in step with sources.yaml: editing the file
    # (or moving an outlet between groups) then applies to the articles already stored.
    for src in config.load_sources(enabled_only=False):
        conn.execute("""UPDATE articles SET scope = ?, language = ?, "group" = ?
                        WHERE source = ? AND (scope != ? OR language != ? OR "group" != ?)""",
                     (src.get("scope", "portugal"), src.get("language", "pt"),
                      src.get("group", "mainstream"), src["id"],
                      src.get("scope", "portugal"), src.get("language", "pt"),
                      src.get("group", "mainstream")))
    # Rebuild rows that are empty or stored in the older, unpadded format.
    # The padding (" palavra ") is what makes whole-word search possible.
    stale = conn.execute(
        """SELECT id, title, summary FROM articles
           WHERE search_text = '' OR search_text NOT LIKE ' %' OR raw_text = ''"""
    ).fetchall()
    if stale:
        from .search import raw_searchable, searchable   # late: search imports config only
        for row in stale:
            text = f"{row['title']} {row['summary']}"
            conn.execute("UPDATE articles SET search_text = ?, raw_text = ? WHERE id = ?",
                         (searchable(text), raw_searchable(text), row["id"]))
    conn.executescript(INDEXES)
    conn.commit()


def connect(db_path=None):
    if db_path is None:
        db_path = config.path(config.load_config()["storage"]["database"])
    if str(db_path) != ":memory:":
        config.path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn
