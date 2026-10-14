"""Bylines, profile resolution and the profile API."""
import json
import os

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


def test_parse_feed_keeps_bylines():
    now = ingest.utcnow()
    item, = ingest.parse_feed(feed_bytes("<dc:creator>Hugo Franco</dc:creator>"), "expresso", now)
    assert item["authors"] == ["Hugo Franco"]
    assert ingest.parse_feed(feed_bytes(), "x", now)[0]["authors"] == []        # no byline
    assert ingest.parse_feed(feed_bytes("<dc:creator>X Y</dc:creator>"), "x", now,
                             author_from="none")[0]["authors"] == []


def test_an_article_without_bylines_stores_an_empty_list():
    conn = db.connect(":memory:")
    now = ingest.iso(ingest.utcnow())
    ingest.store(conn, [{"source": "expresso", "title": "Uma notícia", "title_norm": "uma noticia",
                         "summary": "", "url": "https://x/1", "published_at": now, "fetched_at": now}])
    assert conn.execute("SELECT authors FROM articles").fetchone()[0] == "[]"


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


def claims(profile):
    for key in ("funding", "narrative"):
        yield from profile.get(key) or []
    for key in ("official", "reported"):
        yield from (profile.get("political_links") or {}).get(key) or []


@pytest.mark.parametrize("path", sorted((profiles.PROFILES / "outlets").glob("*.yaml")),
                         ids=lambda p: p.stem)
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
    monkeypatch.setattr(profiles, "settings", lambda: {"stale_after_days": 180})
    today = date(2027, 1, 1)
    assert not profiles.is_stale({"last_checked": date(2026, 9, 27)}, today)
    assert profiles.is_stale({"last_checked": date(2026, 6, 1)}, today)
    assert profiles.is_stale({"last_checked": "2026-06-01"}, today)
    assert profiles.is_stale({}, today)                          # never checked
    assert profiles.stale_profiles(date(2026, 9, 28)) == []      # everything was checked today
    assert len(profiles.stale_profiles(date(2028, 1, 1))) == len(list((profiles.PROFILES / "outlets").glob("*.yaml")))


def test_profiles_status_lists_stale_outlet_profiles(client, monkeypatch):
    monkeypatch.setattr(profiles, "stale_profiles", lambda: [{"id": "rtp", "name": "RTP", "last_checked": "2026-01-01"}])
    data = client.get("/api/profiles/status").json()
    assert data == {"stale_after_days": 180, "stale": [{"id": "rtp", "name": "RTP", "last_checked": "2026-01-01"}]}


def test_journalists_are_names_without_a_profile(client):
    assert client.get("/api/profile/journalist/hugo-franco").status_code == 404
    hugo = profiles.Resolver().resolve("Hugo Franco")
    assert (hugo["kind"], hugo["has_profile"]) == ("journalist", False)


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
    assert sorted(by_name["Mário Rui André"]["outlets"]) == ["lpp", "shifter"]
    outlet_ids = {o["id"] for o in g["outlets"]}
    assert all(l["outlet"] in outlet_ids for l in g["links"])


def test_outlet_profiles_are_read_again_after_an_edit(tmp_path, monkeypatch):
    (tmp_path / "outlets").mkdir()
    path = tmp_path / "outlets" / "jornal.yaml"
    path.write_text("name: Jornal\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "PROFILES", tmp_path)
    assert profiles.outlet("jornal") == {"name": "Jornal"}
    path.write_text("name: Jornal Novo\n", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
    assert profiles.outlet("jornal") == {"name": "Jornal Novo"}
    assert profiles.outlet("outro") == {}
