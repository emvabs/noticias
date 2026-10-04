"""What the reader keeps: saved articles and muted words.

Both live in SQLite rather than the browser so they follow the reader across
devices on the local network (the phone and the computer see the same list).

* Saved articles are a copy (title, summary, outlet, link, date), so they
  survive the feed's cleanup after 14 days.
* Muted words hide every article whose headline or summary contains them,
  with the search box's matching (whole words, synonyms, case-sensitive
  acronyms). They are applied before the positivity mix, so the slider keeps
  its promise on what is left.
"""
from . import search
from .ingest import iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved (
    url          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL,
    scope        TEXT NOT NULL,
    label        TEXT,
    published_at TEXT NOT NULL,
    saved_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS muted (
    term         TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL
);
"""
MAX_TERM = 60


# ---------- saved ----------

def save(conn, article_id):
    """Keep a copy of a stored article. Saving twice keeps the first date."""
    row = conn.execute(
        "SELECT url, title, summary, source, scope, label, published_at FROM articles WHERE id = ?",
        (article_id,)).fetchone()
    if row is None:
        return None
    conn.execute(
        """INSERT OR IGNORE INTO saved (url, title, summary, source, scope, label, published_at, saved_at)
           VALUES (?,?,?,?,?,?,?,?)""", (*tuple(row), iso(utcnow())))
    conn.commit()
    return dict(row)


def unsave(conn, url):
    conn.execute("DELETE FROM saved WHERE url = ?", (url,))
    conn.commit()


def saved(conn):
    """Every saved article, the most recently saved first."""
    rows = conn.execute("SELECT * FROM saved ORDER BY saved_at DESC, published_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------- muted ----------

def clean_term(term):
    """Collapse spaces; refuse an empty term or one too long to be a word or a name."""
    term = " ".join((term or "").split())
    if not search.words(term) or len(term) > MAX_TERM:
        return None
    return term


def mute(conn, term):
    term = clean_term(term)
    if term is None:
        return None
    conn.execute("INSERT OR IGNORE INTO muted (term, created_at) VALUES (?, ?)", (term, iso(utcnow())))
    conn.commit()
    return term


def unmute(conn, term):
    conn.execute("DELETE FROM muted WHERE term = ?", (term,))
    conn.commit()


def muted_terms(conn):
    return [r[0] for r in conn.execute("SELECT term FROM muted ORDER BY created_at, term")]


def muted_clause(terms):
    """(sql, params) matching an article that contains ANY of the terms."""
    clauses, params = [], []
    for term in terms:
        sql, p = search.sql_clause(term)
        if sql:
            clauses.append(sql)
            params += p
    return ("(" + " OR ".join(clauses) + ")", params) if clauses else ("", [])
