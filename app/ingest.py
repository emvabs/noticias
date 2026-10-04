"""Fetch RSS feeds, clean, dedupe, score and store articles."""
import calendar
import html
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from . import archive, clusters, config, db, profiles, topics
from .search import raw_searchable, searchable
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
status = {"last_run": None, "running": False, "sources": {}}


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


def fill_page_authors(conn, client, limit):
    """Read bylines from the pages of articles whose feed has none (newest first).

    Capped per run: the backlog after a first start is cleared over a few runs.
    A page that answers with an error is marked as having no byline, so it is
    not asked for again; a timeout leaves it for the next run.
    """
    page_sources = [s["id"] for s in config.load_sources(enabled_only=False)
                    if s.get("author_from") == "page"]
    if not page_sources or not limit:
        return 0
    rows = conn.execute(
        f"""SELECT id, url FROM articles WHERE authors IS NULL
            AND source IN ({','.join('?' * len(page_sources))})
            ORDER BY published_at DESC LIMIT ?""", page_sources + [limit]).fetchall()
    done = 0
    for r in rows:
        try:
            resp = client.get(r["url"])
            names = profiles.page_authors(resp.text) if resp.is_success else []
        except httpx.HTTPError:
            continue
        conn.execute("UPDATE articles SET authors = ? WHERE id = ?",
                     (json.dumps(names, ensure_ascii=False), r["id"]))
        conn.commit()   # never hold the write lock across a network request
        done += 1
    return done


def rescore_all(conn):
    """Re-apply the current lexicons to every stored article (cheap: a few hundred rows)."""
    rows = conn.execute("SELECT id, title, summary, language FROM articles").fetchall()
    for r in rows:
        score, label, matched = score_article(dict(r))
        conn.execute("UPDATE articles SET score=?, label=?, matched_words=? WHERE id=?",
                     (score, label, matched, r["id"]))
    conn.commit()
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


def fetch_all(conn=None):
    """Fetch every enabled source. Safe to call from several threads (runs one at a time)."""
    if not _lock.acquire(blocking=False):
        return {"skipped": True, "reason": "already running"}
    status["running"] = True
    try:
        cfg = config.load_config()
        own_conn = conn is None
        conn = conn or db.connect()
        retention = cfg["storage"].get("retention_days", 14)
        results = {}
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
                    try:
                        resp = client.get(url)
                        resp.raise_for_status()
                        now = utcnow()
                        items = [a for a in parse_feed(resp.content, src["id"], now,
                                                       src.get("scope", "portugal"),
                                                       src.get("language", "pt"), group,
                                                       src.get("author_from", "feed"))
                                 if a["published_at"] >= cutoff]
                        if not items:
                            errors.append(f"{url}: feed vazio")
                        found += len(items)
                        new += store(conn, items)
                    except Exception as ex:  # one broken feed must not stop the others
                        errors.append(f"{url}: {type(ex).__name__}: {ex}")
                results[src["id"]] = {"name": src["name"], "scope": src.get("scope", "portugal"),
                                      "group": group, "found": found, "new": new,
                                      "errors": errors, "ok": not errors}
            bylines = fill_page_authors(conn, client, cfg["fetch"].get("author_pages_per_run", 60))
        rescored = rescore_all(conn)
        archive.sync(conn)               # before cleanup: the archive keeps what it deletes
        deleted = cleanup(conn, retention)
        topic_counts = {f"{scope}/{group}": len(found)
                        for (scope, group), found in topics.recompute_all(conn).items()}
        archived = archive.sync(conn)    # again, to keep the topics just computed
        stories = sum(len(found) for found in clusters.recompute_all(conn).values())
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        if own_conn:
            conn.close()
        status["last_run"] = iso(utcnow())
        status["sources"] = results
        return {"sources": results, "deleted": deleted, "rescored": rescored, "bylines": bylines, "archived": archived,
                "topics": topic_counts, "clusters": stories, "total": total, "finished_at": status["last_run"]}
    finally:
        status["running"] = False
        _lock.release()


if __name__ == "__main__":
    print(json.dumps(fetch_all(), indent=2, ensure_ascii=False))
