"""Bylines, profile resolution and the profile API."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, ingest, main, profiles


@pytest.mark.parametrize("raw, names", [
    ("Hugo Franco", ["Hugo Franco"]),
    ("Robert Tait in Washington", ["Robert Tait"]),
    ("Rachel Savage in Johannesburg and agencies", ["Rachel Savage"]),
    ("Donna Ferguson and agencies", ["Donna Ferguson"]),
    ("Nick Visser (earlier)", ["Nick Visser"]),
    ("Ed Walton for MetDesk", ["Ed Walton"]),
    ("Amy Hawkins Senior China correspondent", ["Amy Hawkins"]),
    ("Tobi Thomas Health and inequalities correspondent", ["Tobi Thomas"]),
    ("Mark Brown, Lisa O’Carroll and Shane Harrison",
     ["Mark Brown", "Lisa O’Carroll", "Shane Harrison"]),
    ("Shah Meer Baloch in Islamabad and Hannah Ellis-Petersen in Delhi",
     ["Shah Meer Baloch", "Hannah Ellis-Petersen"]),
    ("Pedro Adão e Silva", ["Pedro Adão e Silva"]),          # surname, not two people
    ("Ana Sousa e Rui Costa", ["Ana Sousa", "Rui Costa"]),
    ("Patr&#xED;cia Carvalho", ["Patrícia Carvalho"]),
    ("Maria  Remaevich", ["Maria Remaevich"]),
    ("publico@publico.pt", []),
    ("Presented by Lucy Hough with Devika Bhat; produced by Angus Neale", []),
    ("", []),
])
def test_split_authors(raw, names):
    assert profiles.split_authors(raw) == names


def test_page_authors_reads_json_ld_then_meta():
    ld = ('<script type="application/ld+json">{"@type":"NewsArticle","author":'
          '[{"@type":"Person","name":"Sean Seddon"},{"@type":"Person","name":"Thuthuka Zondi"}]}'
          '</script>')
    assert profiles.page_authors(ld) == ["Sean Seddon", "Thuthuka Zondi"]
    graph = ('<script type="application/ld+json">{"@graph":[{"@type":"WebPage"},'
             '{"@type":"NewsArticle","author":{"@type":"Person","name":"Dang Yuan"}}]}</script>')
    assert profiles.page_authors(graph) == ["Dang Yuan"]
    meta = '<meta name="author" content="Patrick Keddie,Rohan Sharma">'
    assert profiles.page_authors(meta) == ["Patrick Keddie", "Rohan Sharma"]
    # BBC puts its Facebook page in <meta name="author">: not a byline
    assert profiles.page_authors('<meta name="author" content="https://www.facebook.com/bbcnews">') == []
    assert profiles.page_authors('<script type="application/ld+json">{broken</script>') == []


def test_resolver_maps_agencies_outlets_and_people():
    r = profiles.Resolver()
    assert r.resolve("Agência Lusa")["id"] == "lusa"
    assert r.resolve("DN/Lusa")["id"] == r.resolve("Lusa")["id"] == "lusa"
    assert (r.resolve("Expresso")["kind"], r.resolve("Expresso")["id"]) == ("outlet", "expresso")
    assert r.resolve("Vários autores")["kind"] == "other"
    person = r.resolve("João Gabriel Ribeiro")
    assert (person["kind"], person["id"]) == ("journalist", "joao-gabriel-ribeiro")


def test_resolve_all_drops_duplicates():
    r = profiles.Resolver()
    assert [a["id"] for a in r.resolve_all(json.dumps(["Lusa", "Agência Lusa"]))] == ["lusa"]
    assert r.resolve_all(None) == []


def feed_bytes(author_xml=""):
    return f"""<?xml version="1.0"?><rss version="2.0"
        xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>t</title>
        <item><title>Uma notícia</title><link>https://x/1</link>{author_xml}
        <pubDate>Sun, 27 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>""".encode()


def test_parse_feed_keeps_bylines_and_marks_page_sources():
    now = ingest.utcnow()
    item, = ingest.parse_feed(feed_bytes("<dc:creator>Hugo Franco</dc:creator>"), "expresso", now)
    assert item["authors"] == ["Hugo Franco"]
    # no byline: an empty list, or None ("look at the page") for author_from: page
    assert ingest.parse_feed(feed_bytes(), "x", now)[0]["authors"] == []
    assert ingest.parse_feed(feed_bytes(), "x", now, author_from="page")[0]["authors"] is None
    assert ingest.parse_feed(feed_bytes("<dc:creator>X Y</dc:creator>"), "x", now,
                             author_from="none")[0]["authors"] == []


def test_store_fills_bylines_of_articles_stored_before():
    conn = db.connect(":memory:")
    now = ingest.iso(ingest.utcnow())
    art = {"source": "expresso", "title": "Uma notícia", "title_norm": "uma noticia",
           "summary": "", "url": "https://x/1", "published_at": now, "fetched_at": now}
    ingest.store(conn, [{**art, "authors": None}])
    ingest.store(conn, [{**art, "authors": ["Hugo Franco"]}])
    ingest.store(conn, [{**art, "authors": ["Outra Pessoa"]}])     # a known byline is kept
    assert conn.execute("SELECT authors FROM articles").fetchone()[0] == '["Hugo Franco"]'


class FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        import httpx
        if url not in self.pages:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, text=self.pages[url], request=httpx.Request("GET", url))


def test_fill_page_authors_reads_pages_once(monkeypatch):
    monkeypatch.setattr(profiles.config, "load_sources", lambda enabled_only=True: [
        {"id": "bbc", "name": "BBC News", "author_from": "page", "feeds": ["f"]}])
    conn = db.connect(":memory:")
    now = ingest.iso(ingest.utcnow())
    for n in (1, 2):
        ingest.store(conn, [{"source": "bbc", "title": f"Story {n}", "title_norm": f"story {n}",
                             "summary": "", "url": f"https://bbc/{n}", "published_at": now,
                             "fetched_at": now, "language": "en", "authors": None}])
    client = FakeClient({"https://bbc/1": '<meta name="author" content="Sean Seddon">'})
    assert ingest.fill_page_authors(conn, client, 10) == 2
    rows = dict(conn.execute("SELECT url, authors FROM articles").fetchall())
    assert rows == {"https://bbc/1": '["Sean Seddon"]', "https://bbc/2": "[]"}
    assert ingest.fill_page_authors(conn, client, 10) == 0   # nothing left to look up


@pytest.fixture
def client(monkeypatch):
    conn = db.connect(":memory:")
    now = ingest.iso(ingest.utcnow())

    def add(n, source, authors, title):
        ingest.store(conn, [{"source": source, "title": title, "title_norm": ingest.normalize_title(title),
                             "summary": "", "url": f"https://x/{n}", "published_at": now,
                             "fetched_at": now, "authors": authors}])

    add(1, "expresso", ["Hugo Franco"], "Incêndio destrói casa em Sintra")
    add(2, "observador", ["Agência Lusa"], "Greve dos comboios afeta o país")
    add(3, "dn", ["DN/Lusa"], "Chuva forte no norte")
    monkeypatch.setattr(main, "conn", conn)
    return TestClient(main.app)


def test_feed_articles_carry_resolved_authors(client):
    articles = client.get("/api/feed?positive_pct=0&group=").json()["articles"]
    by_title = {a["title"]: a["authors"] for a in articles}
    assert by_title["Incêndio destrói casa em Sintra"][0]["id"] == "hugo-franco"
    assert by_title["Greve dos comboios afeta o país"][0]["kind"] == "outlet"


def test_journalist_profile_has_local_stats(client):
    data = client.get("/api/profile/journalist/hugo-franco").json()
    assert data["stats"]["articles"] == 1
    assert data["stats"]["outlets"] == ["Expresso"]
    assert client.get("/api/profile/journalist/ninguem").status_code == 404
    assert client.get("/api/profile/journalist/..%2Fconfig").status_code == 404


def test_agency_stats_count_bylines_across_outlets(client):
    data = client.get("/api/profile/outlet/lusa").json()
    assert data["stats"]["articles"] == 2
    assert sorted(data["stats"]["outlets"]) == ["Diário de Notícias", "Observador"]


def test_outlet_profile_of_a_source(client):
    data = client.get("/api/profile/outlet/expresso").json()
    assert data["name"] == "Expresso" and data["stats"]["articles"] == 1
    assert client.get("/api/profile/outlet/nao-existe").status_code == 404


def claims(profile):
    for key in ("funding", "career", "narrative"):
        yield from profile.get(key) or []
    for key in ("official", "reported"):
        yield from (profile.get("political_links") or {}).get(key) or []


@pytest.mark.parametrize("path", sorted((profiles.PROFILES / "outlets").glob("*.yaml"))
                         + sorted((profiles.PROFILES / "journalists").glob("*.yaml")),
                         ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_profile_claim_cites_a_source(path):
    """The rule the profiles live by: no claim without a source."""
    data = profiles._read(path)
    assert data.get("name") and data.get("last_checked"), "name and last_checked are required"
    assert profiles.SLUG_RE.match(path.stem), "file names are slugs: lowercase, no accents"
    for item in claims(data):
        assert item.get("text"), item
        sources = item.get("source")
        sources = sources if isinstance(sources, list) else [sources]
        assert sources and all(isinstance(s, str) and s.startswith("https://") for s in sources), item


def test_journalist_files_are_named_after_their_slug():
    for path in (profiles.PROFILES / "journalists").glob("*.yaml"):
        assert profiles.slugify(profiles._read(path)["name"]) == path.stem, path.name


@pytest.mark.parametrize("byline, kind, ident", [
    ("Redação CNN Portugal", "outlet", "cnn"),        # works even without the alias
    ("Al Jazeera Staff", "outlet", "aljazeera"),
    ("BBC Africa", "outlet", "bbc"),
    ("Redação Observador", "outlet", "observador"),
    ("Agências", "other", None),
    ("Equipa de reportagem", "other", None),
    ("Newsroom staff", "other", None),
    ("Hugo Franco", "journalist", "hugo-franco"),
    ("Ana Lusa Pereira", "outlet", "lusa"),            # known limit: aliases.txt fixes it
])
def test_non_person_bylines_are_recognised(monkeypatch, byline, kind, ident):
    monkeypatch.setattr(profiles, "load_aliases", lambda: {})
    r = profiles.Resolver().resolve(byline)
    assert (r["kind"], r["id"]) == (kind, ident)


def test_aliases_still_win_over_the_guess():
    assert profiles.Resolver().resolve("CNN Brasil")["kind"] == "other"


def test_stale_profiles_follow_the_configured_age(monkeypatch):
    from datetime import date
    monkeypatch.setattr(profiles, "settings", lambda: {"stale_after_days": 180,
                                                       "pending_min_articles": 3})
    today = date(2027, 1, 1)
    assert not profiles.is_stale({"last_checked": date(2026, 9, 27)}, today)
    assert profiles.is_stale({"last_checked": date(2026, 6, 1)}, today)
    assert profiles.is_stale({"last_checked": "2026-06-01"}, today)
    assert profiles.is_stale({}, today)                          # never checked
    assert profiles.stale_profiles(date(2026, 9, 28)) == []      # everything was checked today
    assert len(profiles.stale_profiles(date(2028, 1, 1))) == len(list(profiles.PROFILES.glob("*/*.yaml")))


def test_suspect_bylines():
    assert profiles.suspect("mdalmeida") == "uma só palavra"
    assert profiles.suspect("BLITZ") == "uma só palavra"
    assert profiles.suspect("REDAÇÃO SUL") == "tudo em maiúsculas"
    assert profiles.suspect("Hugo Franco") is None


def test_profiles_status_lists_frequent_journalists_without_profile(client, monkeypatch):
    monkeypatch.setattr(profiles, "settings", lambda: {"stale_after_days": 180,
                                                       "pending_min_articles": 1})
    data = client.get("/api/profiles/status").json()
    # Hugo Franco has a profile; the fixture's other bylines are agencies
    assert data["pending"] == [] and data["min_articles"] == 1
    assert data["stale"] == []


def test_profiles_status_counts_new_bylines(monkeypatch):
    conn = db.connect(":memory:")
    now = ingest.iso(ingest.utcnow())
    for n in range(3):
        ingest.store(conn, [{"source": "expresso", "title": f"Notícia {n}", "title_norm": f"noticia {n}",
                             "summary": "", "url": f"https://x/{n}", "published_at": now,
                             "fetched_at": now, "authors": ["Nome Novo"]}])
    monkeypatch.setattr(main, "conn", conn)
    data = TestClient(main.app).get("/api/profiles/status").json()
    assert [(p["slug"], p["articles"]) for p in data["pending"]] == [("nome-novo", 3)]


def test_profile_responses_say_when_stale(client, monkeypatch):
    monkeypatch.setattr(profiles, "is_stale", lambda p, today=None: True)
    assert client.get("/api/profile/journalist/hugo-franco").json()["stale"] is True


GENERIC_WORDS = {"Governo", "Estado", "Government", "State", "Presidente", "President",
                 "Parlamento", "Parliament", "Ministério", "Ministry"}


@pytest.mark.parametrize("path", sorted((profiles.PROFILES / "outlets").glob("*.yaml")),
                         ids=lambda p: p.stem)
def test_outlet_owners_and_keywords(path):
    from app import config, ownership
    data = profiles._read(path)
    owners = data.get("owners")
    assert owners, "every outlet profile says who owns it"
    for o in owners:
        assert o.get("name") and o.get("kind") in ownership.OWNER_KINDS, o
        assert str(o.get("source", "")).startswith("https://"), o
        assert o.get("share") is None or 0 < o["share"] <= 100, o
    assert sum(o["share"] or 0 for o in owners) <= 100.5
    own_names = {data.get("name", ""), path.stem} | {
        s["name"] for s in config.load_sources(enabled_only=False) if s["id"] == path.stem}
    for keyword in data.get("owner_keywords") or []:
        assert keyword not in GENERIC_WORDS, f"{keyword!r} would flag half the news"
        # "disse ao Observador" is attribution, not a conflict of interest
        assert keyword not in own_names, f"{keyword!r} is the outlet's own name"
        assert " " in keyword or keyword[0].isupper(), f"{keyword!r}: a proper name or a phrase"


def test_ownership_graph_merges_shared_owners():
    from app import ownership
    g = ownership.graph()
    by_name = {o["name"]: o for o in g["owners"]}
    assert sorted(by_name["Estado português"]["outlets"]) == ["lusa", "rtp"]
    assert sorted(by_name["Shifter Generation"]["outlets"]) == ["lpp", "shifter"]
    outlet_ids = {o["id"] for o in g["outlets"]}
    assert all(l["outlet"] in outlet_ids for l in g["links"])
