"""Fetch RSS feeds, clean, dedupe, score and store articles."""
import calendar
import html
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

try:
    import fcntl
except ImportError:   # Windows: only the in-process lock applies
    fcntl = None

from . import archive, config, db, profiles, topics
from .search import raw_searchable, searchable
from . import sentiment
from .sentiment import get_scorer, normalize

TAG_RE = re.compile(r"<[^>]+>")
# Footers the feed software appends to every item; they are not part of the piece
# and would otherwise dominate both the topics and the sentiment score.
BOILERPLATE_RES = [
    re.compile(r"\bO (?:conteúdo|artigo|post)\b.*?\b(?:apareceu|aparece)\s+primeiro\s+em\b.*", re.I | re.S),
    re.compile(r"\bThe post\b.*?\bappeared first on\b.*", re.I | re.S),
    re.compile(r"\bContinue reading\.{0,3}\s*$", re.I),
    re.compile(r"\bLer mais\b.*$", re.I),
]
SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^a-z0-9 ]+")

_lock = threading.Lock()
RUN_HISTORY_DAYS = 7   # fetch_runs / feed_results kept this long


def lock_path():
    """The file that marks a fetch in progress, next to the database."""
    db_path = config.path(config.load_config()["storage"]["database"])
    return db_path.with_name(db_path.name + ".fetch.lock")


@contextmanager
def _file_lock():
    """Hold the cross-process fetch lock; yields False if another process has it.

    The web server (button, embedded scheduler) and ``python -m app.worker`` are
    separate processes, so the thread lock alone cannot stop them fetching at once.
    """
    if fcntl is None:
        yield True
        return
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def is_running():
    """Whether a fetch is running in this process or any other."""
    if _lock.locked():
        return True
    with _file_lock() as got:
        return not got


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summary_limit():
    return config.load_config()["storage"].get("summary_max_chars", 300)


def clean_text(raw, max_len=None):
    max_len = max_len or summary_limit()
    # Some feeds (CNN Portugal) escape their HTML twice, so unescape and strip
    # repeatedly until nothing changes, instead of assuming a single pass.
    text = raw or ""
    for _ in range(3):
        cleaned = TAG_RE.sub(" ", html.unescape(text))
        if cleaned == text:
            break
        text = cleaned
    for pattern in BOILERPLATE_RES:
        text = pattern.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def normalize_title(title):
    return SPACE_RE.sub(" ", PUNCT_RE.sub(" ", normalize(title))).strip()


def entry_datetime(entry, fallback):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            dt = datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
            return min(dt, fallback)  # guard against future-dated items
    return fallback


def parse_feed(content, source_id, now, scope="portugal", language="pt", group="mainstream",
               author_from="feed"):
    """Turn raw feed bytes into article dicts (unscored).

    ``authors`` is None when the byline is still to be read from the article
    page (``author_from: page`` in sources.yaml), otherwise a list, maybe empty.
    """
    parsed = feedparser.parse(content)
    items = []
    for e in parsed.entries:
        title = clean_text(e.get("title"), 300)
        url = (e.get("link") or "").strip()
        if not title or not url:
            continue
        summary = clean_text(e.get("summary") or e.get("description"))
        if normalize_title(summary) == normalize_title(title):
            summary = ""
        names = [] if author_from == "none" else profiles.entry_authors(e)
        items.append({
            "source": source_id, "title": title, "title_norm": normalize_title(title),
            "summary": summary, "url": url, "scope": scope, "language": language,
            "group": group, "authors": names or (None if author_from == "page" else []),
            "published_at": iso(entry_datetime(e, now)), "fetched_at": iso(now),
        })
    return items


def search_text(title, summary):
    """Text used by the search box: lowercase, accent-free, space-padded words."""
    return searchable(f"{title} {summary}")


def raw_text(title, summary):
    """The same text with capitals and accents, for case-sensitive acronyms."""
    return raw_searchable(f"{title} {summary}")


def score_article(art, scorer=None):
    """Score one article with the lexicon of its own language."""
    scorer = scorer or get_scorer(art.get("language") or "pt")
    score, label, matched = scorer.score(f"{art['title']}. {art['summary']}")
    return score, label, json.dumps(matched, ensure_ascii=False)


def store(conn, articles, scorer=None):
    """Insert articles, skipping duplicate URLs / normalized titles. Returns count inserted."""
    inserted = 0
    for a in articles:
        score, label, matched = score_article(a, scorer)
        authors = None if a.get("authors") is None else json.dumps(a["authors"], ensure_ascii=False)
        cur = conn.execute(
            """INSERT OR IGNORE INTO articles
               (source, title, title_norm, summary, url, published_at, fetched_at,
                scope, language, "group", score, label, matched_words, search_text, raw_text,
                authors)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a["source"], a["title"], a["title_norm"], a["summary"], a["url"],
             a["published_at"], a["fetched_at"], a.get("scope", "portugal"),
             a.get("language", "pt"), a.get("group", "mainstream"), score, label, matched,
             search_text(a["title"], a["summary"]),
             raw_text(a["title"], a["summary"]), authors))
        inserted += cur.rowcount
        if not cur.rowcount and authors is not None:
            # Articles stored before bylines were kept get theirs the next time the feed lists them.
            conn.execute("UPDATE articles SET authors = ? WHERE url = ? AND authors IS NULL",
                         (authors, a["url"]))
    conn.commit()
    return inserted


def source_headers(src):
    """Headers for one source's requests: its own User-Agent if sources.yaml sets one."""
    return {"User-Agent": src["user_agent"]} if src.get("user_agent") else None


def fill_page_authors(conn, client, limit):
    """Read bylines from the pages of articles whose feed has none (newest first).

    Capped per run: the backlog after a first start is cleared over a few runs.
    A page that answers with an error is marked as having no byline, so it is
    not asked for again; a timeout leaves it for the next run.
    """
    page_sources = {s["id"]: s for s in config.load_sources(enabled_only=False)
                    if s.get("author_from") == "page"}
    if not page_sources or not limit:
        return 0
    rows = conn.execute(
        f"""SELECT id, source, url FROM articles WHERE authors IS NULL
            AND source IN ({','.join('?' * len(page_sources))})
            ORDER BY published_at DESC LIMIT ?""", list(page_sources) + [limit]).fetchall()
    done = 0
    for r in rows:
        try:
            resp = client.get(r["url"], headers=source_headers(page_sources[r["source"]]))
            names = profiles.page_authors(resp.text) if resp.is_success else []
        except httpx.HTTPError:
            continue
        conn.execute("UPDATE articles SET authors = ? WHERE id = ?",
                     (json.dumps(names, ensure_ascii=False), r["id"]))
        conn.commit()   # never hold the write lock across a network request
        done += 1
    return done


_rescored_with = None   # sentiment.version() of the last full re-score in this process


def rescore_all(conn):
    """Re-apply the current lexicons to every stored article, when they changed.

    New articles are scored on the way in, so a full pass is only needed once
    per process and after a word list, rule file or setting changes.
    """
    global _rescored_with
    version = sentiment.version()
    if version == _rescored_with:
        return 0
    rows = conn.execute("SELECT id, title, summary, language FROM articles").fetchall()
    for r in rows:
        score, label, matched = score_article(dict(r))
        conn.execute("UPDATE articles SET score=?, label=?, matched_words=? WHERE id=?",
                     (score, label, matched, r["id"]))
    conn.commit()
    _rescored_with = version
    return len(rows)


def cleanup(conn, retention_days):
    """Delete old articles, giving each group its own retention if configured.

    The independent outlets publish a few pieces a month, so the general
    14 days would leave their section almost empty.
    """
    deleted = 0
    groups = [r[0] for r in conn.execute('SELECT DISTINCT "group" FROM articles')]
    for group in groups:
        days = config.group_settings(group, "retention_days", default=retention_days) or retention_days
        cur = conn.execute('DELETE FROM articles WHERE "group" = ? AND published_at < ?',
                           (group, iso(utcnow() - timedelta(days=days))))
        deleted += cur.rowcount
    conn.commit()
    return deleted


def start_run(conn):
    run_id = conn.execute("INSERT INTO fetch_runs (started_at) VALUES (?)",
                          (iso(utcnow()),)).lastrowid
    conn.commit()
    return run_id


def record_feed(conn, run_id, source, url, items, error):
    conn.execute("INSERT INTO feed_results (run_id, source, url, items, error, fetched_at)"
                 " VALUES (?,?,?,?,?,?)", (run_id, source, url, items, error, iso(utcnow())))
    conn.commit()


def finish_run(conn, run_id, ok):
    finished = iso(utcnow())
    conn.execute("UPDATE fetch_runs SET finished_at = ?, ok = ? WHERE id = ?",
                 (finished, int(ok), run_id))
    cutoff = iso(utcnow() - timedelta(days=RUN_HISTORY_DAYS))
    conn.execute("DELETE FROM feed_results WHERE fetched_at < ?", (cutoff,))
    conn.execute("DELETE FROM fetch_runs WHERE started_at < ?", (cutoff,))
    conn.commit()
    return finished


def last_finished(conn, ok_only=False):
    """When the last fetch run ended (any process); ``ok_only``: one that got items."""
    sql = "SELECT MAX(finished_at) FROM fetch_runs" + (" WHERE ok = 1" if ok_only else "")
    return conn.execute(sql).fetchone()[0]


def latest_errors(conn):
    """{source: ["url: error", ...]} from each feed's most recent result."""
    rows = conn.execute(
        """SELECT source, url, error FROM feed_results
           JOIN (SELECT url, MAX(run_id) run_id FROM feed_results GROUP BY url) USING (url, run_id)
           WHERE error IS NOT NULL ORDER BY source, url""").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["source"], []).append(f"{r['url']}: {r['error']}")
    return out


def empty_feeds(conn, hours=24):
    """Enabled feeds fetched in the last ``hours`` that returned no items on any run."""
    names = {s["id"]: s["name"] for s in config.load_sources()}
    enabled = {(s["id"], url) for s in config.load_sources() for url in s["feeds"]}
    rows = conn.execute(
        """SELECT source, url, COUNT(*) runs, MAX(items) most,
                  (SELECT error FROM feed_results l WHERE l.url = r.url
                   ORDER BY run_id DESC LIMIT 1) last_error
           FROM feed_results r WHERE fetched_at >= ?
           GROUP BY source, url HAVING most = 0 ORDER BY source, url""",
        (iso(utcnow() - timedelta(hours=hours)),)).fetchall()
    return [{"source": r["source"], "name": names[r["source"]], "url": r["url"],
             "runs": r["runs"], "last_error": r["last_error"]}
            for r in rows if (r["source"], r["url"]) in enabled]


def fetch_all(conn=None):
    """Fetch every enabled source, one run at a time across threads and processes."""
    if not _lock.acquire(blocking=False):
        return {"skipped": True, "reason": "already running"}
    try:
        with _file_lock() as got:
            if not got:
                return {"skipped": True, "reason": "already running"}
            return _fetch_all(conn)
    finally:
        _lock.release()


def _fetch_all(conn):
    """One fetch run; the caller holds the locks."""
    cfg = config.load_config()
    own_conn = conn is None
    conn = conn or db.connect()
    retention = cfg["storage"].get("retention_days", 14)
    results = {}
    run_id = start_run(conn)
    with httpx.Client(headers={"User-Agent": cfg["fetch"]["user_agent"]},
                      timeout=cfg["fetch"].get("timeout_seconds", 20),
                      follow_redirects=True) as client:
        for src in config.load_sources():
            found = new = 0
            errors = []
            group = src.get("group", "mainstream")
            # 0 (or missing) means "no date cutoff": take whatever the feed
            # offers, which is what the slow independent outlets need.
            window_days = config.group_settings(group, "ingest_window_days",
                                                default=retention)
            cutoff = (iso(utcnow() - timedelta(days=window_days))
                      if window_days else "")
            for url in src["feeds"]:
                items, error = [], None
                try:
                    resp = client.get(url, headers=source_headers(src))
                    resp.raise_for_status()
                    now = utcnow()
                    items = [a for a in parse_feed(resp.content, src["id"], now,
                                                   src.get("scope", "portugal"),
                                                   src.get("language", "pt"), group,
                                                   src.get("author_from", "feed"))
                             if a["published_at"] >= cutoff]
                    if not items:
                        error = "feed vazio"
                    found += len(items)
                    new += store(conn, items)
                except Exception as ex:  # one broken feed must not stop the others
                    error = f"{type(ex).__name__}: {ex}"
                if error:
                    errors.append(f"{url}: {error}")
                record_feed(conn, run_id, src["id"], url, len(items), error)
            results[src["id"]] = {"name": src["name"], "scope": src.get("scope", "portugal"),
                                  "group": group, "found": found, "new": new,
                                  "errors": errors, "ok": not errors}
        bylines = fill_page_authors(conn, client, cfg["fetch"].get("author_pages_per_run", 60))
    rescored = rescore_all(conn)
    topic_counts = {f"{scope}/{group}": len(found)
                    for (scope, group), found in topics.recompute_all(conn).items()}
    # After topics, so the archive keeps the ones just computed; before cleanup,
    # so it keeps what retention is about to delete. Topics only read the last
    # few days, so the articles cleanup removes do not change them.
    archived = archive.sync(conn)
    deleted = cleanup(conn, retention)
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    finished = finish_run(conn, run_id, ok=any(r["found"] for r in results.values()))
    if own_conn:
        conn.close()
    return {"sources": results, "deleted": deleted, "rescored": rescored, "bylines": bylines, "archived": archived,
            "topics": topic_counts, "total": total, "finished_at": finished}


if __name__ == "__main__":
    print(json.dumps(fetch_all(), indent=2, ensure_ascii=False))
