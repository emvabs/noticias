"""SQLite storage.  Migrate explicitly with:  python -m app.db migrate"""
import argparse
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
    raw_text      TEXT NOT NULL DEFAULT '',  -- same text with capitals/accents (acronyms)
    authors       TEXT                    -- JSON list of bylines; NULL = unknown (older rows)
);

CREATE TABLE IF NOT EXISTS fetch_runs (     -- one row per fetch, from any process
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,                       -- NULL while running (or if the process died)
    ok          INTEGER NOT NULL DEFAULT 0  -- finished, and at least one feed had items
);

CREATE TABLE IF NOT EXISTS feed_results (   -- what each feed returned on each run
    run_id     INTEGER NOT NULL,
    source     TEXT NOT NULL,
    url        TEXT NOT NULL,
    items      INTEGER NOT NULL,            -- items within the date window, as stored
    error      TEXT,                        -- NULL when fine; "feed vazio" when no items
    fetched_at TEXT NOT NULL
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

# Stamped into PRAGMA user_version by migrate(). Bump it when migrate() gains a
# step, so processes started with NOTICIAS_AUTO_MIGRATE=0 refuse an old database.
SCHEMA_VERSION = 2   # 2: fetch_runs, feed_results

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_label ON articles(label, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_scope ON articles(scope, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_group ON articles(scope, "group", published_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_results_time ON feed_results(fetched_at);
CREATE INDEX IF NOT EXISTS idx_feed_results_url ON feed_results(url, run_id);
"""


def migrate(conn):
    """Create the schema and re-sync scope, language and group from sources.yaml."""
    conn.executescript(SCHEMA)
    # Always keep scope/language/group in step with sources.yaml: editing the file
    # (or moving an outlet between groups) then applies to the articles already stored.
    for src in config.load_sources(enabled_only=False):
        conn.execute("""UPDATE articles SET scope = ?, language = ?, "group" = ?
                        WHERE source = ? AND (scope != ? OR language != ? OR "group" != ?)""",
                     (src.get("scope", "portugal"), src.get("language", "pt"),
                      src.get("group", "mainstream"), src["id"],
                      src.get("scope", "portugal"), src.get("language", "pt"),
                      src.get("group", "mainstream")))
    conn.executescript(INDEXES)
    conn.commit()
    # The archive is filled from the articles already stored (first start after
    # the upgrade) and kept in step on every fetch; see app/archive.py.
    from . import archive                 # late: archive -> ownership -> profiles -> config
    conn.executescript(archive.SCHEMA)
    if not conn.execute("SELECT 1 FROM article_archive LIMIT 1").fetchone():
        archive.sync(conn)   # only the first time: every fetch keeps it in step afterwards
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def connect(db_path=None, run_migrations=None):
    """Open the database, migrating it first unless migrations are switched off.

    ``run_migrations`` defaults to ``config.auto_migrate()`` (on, unless
    NOTICIAS_AUTO_MIGRATE=0). Off, an unmigrated database is an error rather
    than something every web process races to fix. ":memory:" always migrates.
    """
    if db_path is None:
        db_path = config.path(config.load_config()["storage"]["database"])
    memory = str(db_path) == ":memory:"
    if not memory:
        config.path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if run_migrations is None:
        run_migrations = config.auto_migrate()
    if run_migrations or memory:
        migrate(conn)
    else:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            conn.close()
            raise RuntimeError(
                f"database {db_path} is at schema version {version}, this code needs"
                f" {SCHEMA_VERSION}: run  python -m app.db migrate  (or unset NOTICIAS_AUTO_MIGRATE)")
    return conn


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.db")
    parser.add_argument("command", choices=["migrate"])
    parser.add_argument("--db", help="database file (default: storage.database in config.yaml)")
    args = parser.parse_args(argv)
    conn = connect(args.db, run_migrations=True)
    count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    print(f"migrated to schema version {SCHEMA_VERSION} ({count} articles)")


if __name__ == "__main__":
    main()
