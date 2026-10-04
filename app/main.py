"""FastAPI app: JSON API + the single-page frontend."""
import json
import threading
from contextlib import asynccontextmanager
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, deepdive, ingest, ownership, profiles, search
from . import topics as topics_module
from .mixing import collapse, lead, mix, outlets

STATIC = config.ROOT / "static"
_conn_lock = threading.Lock()
conn = db.connect()


@asynccontextmanager
async def lifespan(app):
    minutes = config.load_config()["fetch"].get("interval_minutes", 20)
    scheduler = BackgroundScheduler(timezone="UTC")
    # First run right away (in the background, so the page opens immediately).
    scheduler.add_job(ingest.fetch_all, "interval", minutes=minutes,
                      next_run_time=ingest.utcnow(), id="fetch", max_instances=1)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Notícias", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def always_revalidate(request, call_next):
    """Browsers must re-check the page and its assets, so edits show up on reload."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


DEFAULT_SCOPE = "portugal"
DEFAULT_GROUP = "mainstream"
TODAY_HOURS = 24      # the side column's "today"


def source_names():
    return {s["id"]: s["name"] for s in config.load_sources(enabled_only=False)}


def pool(label_clause, scope, group, sources, topic_slugs, query, limit):
    if sources is not None and not sources:
        return []
    # Filter order: scope -> group -> outlets -> topic/search -> positivity mix.
    sql = f"SELECT * FROM articles WHERE scope = ? AND {label_clause}"
    params = [scope]
    if group:
        sql += ' AND "group" = ?'
        params.append(group)
    if sources is not None:
        sql += f" AND source IN ({','.join('?' * len(sources))})"
        params += sources
    search_sql, search_params = search.sql_clause(query)
    if search_sql:
        sql += f" AND {search_sql}"
        params += search_params
    if topic_slugs:
        # An article matches if it carries ANY of the selected topics.
        sql += (" AND EXISTS (SELECT 1 FROM json_each(articles.topics)"
                f" WHERE json_each.value IN ({','.join('?' * len(topic_slugs))}))")
        params += topic_slugs
    sql += " ORDER BY published_at DESC LIMIT ?"
    with _conn_lock:
        rows = conn.execute(sql, params + [limit]).fetchall()
    return [dict(r) for r in rows]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/feed")
def feed(positive_pct: float = Query(50, ge=0, le=100),
         limit: int | None = Query(None, ge=1),
         scope: str = DEFAULT_SCOPE,
         group: str = DEFAULT_GROUP,
         sources: str | None = None,
         topics: str | None = None,
         q: str | None = None):
    cfg = config.load_config()["feed"]
    limit = min(limit or cfg.get("default_limit", 50), cfg.get("max_limit", 200))
    source_list = [s for s in sources.split(",") if s] if sources is not None else None
    topic_list = [t for t in (topics or "").split(",") if t]

    # One item per story: several outlets' versions collapse into one, so each
    # side is read deeper than the page and then cut down to stories.
    depth = limit * 3 + 50
    positive = collapse(pool("label = 'positive'", scope, group, source_list, topic_list, q, depth))
    non_positive = collapse(pool("label != 'positive'", scope, group, source_list, topic_list, q, depth))
    articles, stats = mix(positive, non_positive, positive_pct, limit)
    top = lead(articles)

    names = source_names()
    resolver = profiles.Resolver()
    owner_keywords = ownership.keywords_by_source()
    for a in articles:
        # The other outlets' versions of the story, all on the same side of the slider.
        a["outlets"] = len(outlets(a))
        a["also"] = [{"id": o["id"], "source": o["source"], "source_name": names.get(o["source"], o["source"]),
                      "title": o["title"], "url": o["url"], "label": o["label"],
                      "published_at": o["published_at"]} for o in a["also"]]
        a["authors"] = resolver.resolve_all(a["authors"])
        # The outlet's own owner, group or sister company named in the piece.
        a["owner_mentions"] = ownership.mentions(a["raw_text"], owner_keywords.get(a["source"], []))
        a["source_name"] = names.get(a["source"], a["source"])
        a["matched_words"] = json.loads(a["matched_words"] or "[]")
        a["topics"] = json.loads(a["topics"] or "[]")
        a.pop("title_norm", None)
    # The page shows which synonyms were searched as well as the typed words.
    expanded = search.expand(q)[1:] if q else []
    return {**stats, "lead_id": top["id"] if top else None, "scope": scope, "group": group, "topics": topic_list, "q": (q or "").strip(),
            "also_searched": expanded, "last_updated": ingest.status["last_run"], "articles": articles}


@app.get("/api/sources")
def sources(scope: str | None = None, group: str | None = None):
    """Outlets, optionally only those of one scope (portugal / world) or group."""
    with _conn_lock:
        rows = conn.execute(
            "SELECT source, COUNT(*) n, MAX(published_at) last FROM articles GROUP BY source"
        ).fetchall()
    stats = {r["source"]: (r["n"], r["last"]) for r in rows}
    owners = {ident: [o["name"] for o in p.get("owners") or []]
              for ident, p in ownership.outlet_profiles().items()}
    out = []
    for s in config.load_sources(enabled_only=False):
        if scope and s.get("scope", DEFAULT_SCOPE) != scope:
            continue
        if group and s.get("group", DEFAULT_GROUP) != group:
            continue
        st = ingest.status["sources"].get(s["id"], {})
        count, last = stats.get(s["id"], (0, None))
        out.append({"id": s["id"], "name": s["name"], "scope": s.get("scope", DEFAULT_SCOPE),
                    "language": s.get("language", "pt"), "group": s.get("group", DEFAULT_GROUP),
                    "enabled": bool(s.get("enabled", True) and s.get("feeds")),
                    "articles": count, "last_published": last, "errors": st.get("errors", []),
                    "owners": owners.get(s["id"], [])})
    return out


@app.get("/api/topics")
def topics_endpoint(scope: str = DEFAULT_SCOPE, group: str = DEFAULT_GROUP):
    """Current topics for a scope and group, most articles first."""
    with _conn_lock:
        return topics_module.current(conn, scope, group)


@app.get("/api/profile/outlet/{ident}")
def outlet_profile(ident: str):
    """Who owns and funds an outlet (researched file) plus what this app has seen of it."""
    if not profiles.SLUG_RE.match(ident):
        raise HTTPException(404)
    profile = profiles.outlet(ident)
    name = profile.get("name") or source_names().get(ident)
    with _conn_lock:
        stats = profiles.outlet_stats(conn, ident)
    if not name and not stats["articles"]:
        raise HTTPException(404)
    return {"id": ident, "name": name, "profile": profile or None,
            "stale": bool(profile) and profiles.is_stale(profile), "stats": stats}


@app.get("/api/profile/journalist/{slug}")
def journalist_profile(slug: str):
    """A journalist's researched profile, if there is one, plus their articles here."""
    if not profiles.SLUG_RE.match(slug):
        raise HTTPException(404)
    profile = profiles.journalist(slug)
    with _conn_lock:
        stats = profiles.journalist_stats(conn, slug)
    if not profile and not stats["articles"]:
        raise HTTPException(404)
    return {"id": slug, "name": profile.get("name"), "profile": profile or None,
            "stale": bool(profile) and profiles.is_stale(profile), "stats": stats}


@app.get("/api/profiles/status")
def profiles_status():
    """What still needs research: frequent journalists without a profile, old profiles."""
    cfg = profiles.settings()
    with _conn_lock:
        todo = [p for p in profiles.pending(conn) if p["articles"] >= cfg["pending_min_articles"]]
    return {"min_articles": cfg["pending_min_articles"], "pending": todo,
            "stale_after_days": cfg["stale_after_days"], "stale": profiles.stale_profiles()}


@app.get("/api/deepdive/index")
def deepdive_index(kind: str = Query("outlet", pattern="^(outlet|journalist)$")):
    """Outlets or journalists that can be chosen, most articles first."""
    with _conn_lock:
        return deepdive.index(conn, kind)


@app.get("/api/deepdive/{kind}/{ident}")
def deepdive_entity(kind: str, ident: str):
    """Summary, researched and data findings, and the articles behind each one."""
    if kind not in ("outlet", "journalist") or not profiles.SLUG_RE.match(ident):
        raise HTTPException(404)
    with _conn_lock:
        result = deepdive.dive(conn, kind, ident)
    if not result["articles"] and not result["has_profile"]:
        raise HTTPException(404)
    return result


@app.get("/api/ownership")
def ownership_map():
    """Owners -> outlets, for the ownership graph."""
    return ownership.graph()


@app.get("/api/silences")
def silences(scope: str = DEFAULT_SCOPE):
    """Trending topics x outlets: who covered each topic less than their volume predicts."""
    with _conn_lock:
        return deepdive.silences(conn, scope)


@app.get("/api/silences/articles")
def silence_articles(topic: str, scope: str = DEFAULT_SCOPE, outlet: str | None = None):
    with _conn_lock:
        return deepdive.silence_articles(conn, scope, topic, outlet)


@app.get("/api/today")
def today(scope: str = DEFAULT_SCOPE, group: str = DEFAULT_GROUP):
    """The side column: the last 24 hours' tone, and the day's strongest silence."""
    since = ingest.iso(ingest.utcnow() - timedelta(hours=TODAY_HOURS))
    with _conn_lock:
        rows = conn.execute(
            'SELECT label, COUNT(*) n FROM articles WHERE scope = ? AND "group" = ?'
            " AND published_at >= ? GROUP BY label", (scope, group, since)).fetchall()
        # Silences compare mainstream outlets on the topics trending now.
        silence = None
        if group == DEFAULT_GROUP:
            trending = {t["slug"] for t in topics_module.current(conn, scope, DEFAULT_GROUP)}
            silence = deepdive.strongest_silence(deepdive.silences(conn, scope), trending)
    labels = {"positive": 0, "neutral": 0, "negative": 0}
    labels.update({r["label"]: r["n"] for r in rows if r["label"] in labels})
    return {"scope": scope, "group": group, "window_hours": TODAY_HOURS,
            "labels": labels, "total": sum(labels.values()), "silence": silence}


@app.get("/api/status")
def status():
    return {"last_updated": ingest.status["last_run"], "running": ingest.status["running"]}


@app.post("/api/refresh")
def refresh():
    # Runs in the request thread (FastAPI threadpool); takes a couple of seconds.
    result = ingest.fetch_all()
    if result.get("skipped"):
        return {"ok": True, "skipped": True, "last_updated": ingest.status["last_run"]}
    return {"ok": True, **result}
