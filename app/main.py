"""FastAPI app: JSON API + the single-page frontend."""
import json
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, ingest, search
from . import topics as topics_module
from .mixing import mix

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

    positive = pool("label = 'positive'", scope, group, source_list, topic_list, q, limit)
    non_positive = pool("label != 'positive'", scope, group, source_list, topic_list, q, limit)
    articles, stats = mix(positive, non_positive, positive_pct, limit)

    names = source_names()
    for a in articles:
        a["source_name"] = names.get(a["source"], a["source"])
        a["matched_words"] = json.loads(a["matched_words"] or "[]")
        a["topics"] = json.loads(a["topics"] or "[]")
        a.pop("title_norm", None)
    # The page shows which synonyms were searched as well as the typed words.
    expanded = search.expand(q)[1:] if q else []
    return {**stats, "scope": scope, "group": group, "topics": topic_list, "q": (q or "").strip(),
            "also_searched": expanded, "last_updated": ingest.status["last_run"], "articles": articles}


@app.get("/api/sources")
def sources(scope: str | None = None, group: str | None = None):
    """Outlets, optionally only those of one scope (portugal / world) or group."""
    with _conn_lock:
        rows = conn.execute(
            "SELECT source, COUNT(*) n, MAX(published_at) last FROM articles GROUP BY source"
        ).fetchall()
    stats = {r["source"]: (r["n"], r["last"]) for r in rows}
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
                    "articles": count, "last_published": last, "errors": st.get("errors", [])})
    return out


@app.get("/api/topics")
def topics_endpoint(scope: str = DEFAULT_SCOPE, group: str = DEFAULT_GROUP):
    """Current topics for a scope and group, most articles first."""
    with _conn_lock:
        return topics_module.current(conn, scope, group)


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
