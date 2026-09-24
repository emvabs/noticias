"""Fetch RSS feeds, clean, dedupe, score and store articles."""
import calendar
import html
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from . import config, db, topics
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


def parse_feed(content, source_id, now, scope="portugal", language="pt", group="mainstream"):
    """Turn raw feed bytes into article dicts (unscored)."""
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
        items.append({
            "source": source_id, "title": title, "title_norm": normalize_title(title),
            "summary": summary, "url": url, "scope": scope, "language": language,
            "group": group,
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
        cur = conn.execute(
            """INSERT OR IGNORE INTO articles
               (source, title, title_norm, summary, url, published_at, fetched_at,
                scope, language, "group", score, label, matched_words, search_text, raw_text)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a["source"], a["title"], a["title_norm"], a["summary"], a["url"],
             a["published_at"], a["fetched_at"], a.get("scope", "portugal"),
             a.get("language", "pt"), a.get("group", "mainstream"), score, label, matched,
             search_text(a["title"], a["summary"]),
             raw_text(a["title"], a["summary"])))
        inserted += cur.rowcount
    conn.commit()
    return inserted


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
                                                       src.get("language", "pt"), group)
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
        rescored = rescore_all(conn)
        deleted = cleanup(conn, retention)
        topic_counts = {f"{scope}/{group}": len(found)
                        for (scope, group), found in topics.recompute_all(conn).items()}
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        if own_conn:
            conn.close()
        status["last_run"] = iso(utcnow())
        status["sources"] = results
        return {"sources": results, "deleted": deleted, "rescored": rescored,
                "topics": topic_counts, "total": total, "finished_at": status["last_run"]}
    finally:
        status["running"] = False
        _lock.release()


if __name__ == "__main__":
    print(json.dumps(fetch_all(), indent=2, ensure_ascii=False))
