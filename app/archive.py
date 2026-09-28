"""A lightweight, never-deleted copy of every article, for the Deep Dive.

The feed keeps 14 days (``articles`` is cleaned up); trends about an outlet or
a journalist need months. The archive keeps the fields those trends use -
title, bylines, sentiment, topics, owner mentions - but not the summary, so it
grows by roughly 50 MB a year.

Topics are *accumulated*: ``articles.topics`` only holds the topics trending
right now and is reset on every recompute, so copying it would forget them.
"""
import json

from . import ownership

SCHEMA = """
CREATE TABLE IF NOT EXISTS article_archive (
    url            TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    scope          TEXT NOT NULL,
    "group"        TEXT NOT NULL,
    language       TEXT NOT NULL,
    published_at   TEXT NOT NULL,
    title          TEXT NOT NULL,
    authors        TEXT,                     -- JSON list of bylines; NULL = unknown
    label          TEXT,
    score          REAL,
    topics         TEXT NOT NULL DEFAULT '[]', -- every topic it was ever tagged with
    owner_mentions TEXT NOT NULL DEFAULT '[]'  -- owner keywords its text mentions
);
CREATE INDEX IF NOT EXISTS idx_archive_scope ON article_archive(scope, published_at);
CREATE INDEX IF NOT EXISTS idx_archive_source ON article_archive(source, published_at);

CREATE TABLE IF NOT EXISTS topic_labels (    -- labels of topics no longer trending
    scope     TEXT NOT NULL,
    slug      TEXT NOT NULL,
    label     TEXT NOT NULL,
    PRIMARY KEY (scope, slug)
);
"""


def _merge_topics(old, new):
    merged = json.loads(old or "[]")
    for slug in json.loads(new or "[]"):
        if slug not in merged:
            merged.append(slug)
    return json.dumps(merged)


def sync(conn):
    """Copy the current articles into the archive (insert new, refresh the rest).

    Label, score and bylines follow the live article (re-scoring and late
    page bylines apply); topics are unioned. Rows whose article was deleted
    by retention keep their last values.
    """
    keywords = ownership.keywords_by_source()
    rows = conn.execute(
        """SELECT a.url, a.source, a.scope, a."group", a.language, a.published_at, a.title,
                  a.authors, a.label, a.score, a.topics, a.raw_text, ar.topics AS archived_topics
           FROM articles a LEFT JOIN article_archive ar ON ar.url = a.url""").fetchall()
    conn.executemany(
        """INSERT INTO article_archive
               (url, source, scope, "group", language, published_at, title, authors,
                label, score, topics, owner_mentions)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
               scope = excluded.scope, "group" = excluded."group",
               language = excluded.language, authors = excluded.authors,
               label = excluded.label, score = excluded.score,
               topics = excluded.topics, owner_mentions = excluded.owner_mentions""",
        [(r["url"], r["source"], r["scope"], r["group"], r["language"], r["published_at"],
          r["title"], r["authors"], r["label"], r["score"],
          _merge_topics(r["archived_topics"], r["topics"]),
          json.dumps(ownership.mentions(r["raw_text"], keywords.get(r["source"], [])),
                     ensure_ascii=False))
         for r in rows])
    has_cache = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'topics_cache'").fetchone()
    if has_cache:   # absent only while an old database is being migrated
        conn.execute("""INSERT INTO topic_labels (scope, slug, label)
                        SELECT scope, slug, label FROM topics_cache WHERE true
                        ON CONFLICT(scope, slug) DO UPDATE SET label = excluded.label""")
    conn.commit()
    return len(rows)


def labels(conn, scope):
    """slug -> label for every topic ever seen in a scope."""
    return dict(conn.execute("SELECT slug, label FROM topic_labels WHERE scope = ?", (scope,)))
