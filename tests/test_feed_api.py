"""Feed API: scope, outlet, topic and search filters."""
import pytest
from fastapi.testclient import TestClient

from app import db, ingest, main, topics


@pytest.fixture
def client(monkeypatch):
    """A client backed by a small in-memory database."""
    conn = db.connect(":memory:")
    now = ingest.utcnow()

    def add(title, source="publico", scope="portugal", language="pt", summary=""):
        ingest.store(conn, [{
            "source": source, "title": title, "title_norm": ingest.normalize_title(title),
            "summary": summary, "url": f"https://x/{abs(hash(title))}", "scope": scope,
            "language": language, "published_at": ingest.iso(now), "fetched_at": ingest.iso(now),
        }])

    add("Incêndio destrói casa em Sintra")
    add("Incêndios no norte obrigam a evacuação", source="rtp")
    add("Vitória histórica do Benfica na Luz", source="rtp")
    add("Greve dos comboios afeta o país")
    add("Wildfire spreads across California", source="bbc", scope="world", language="en")

    monkeypatch.setattr(main, "conn", conn)
    return TestClient(main.app)


# The mix is exact now, so these fixtures (which hold no positive articles for
# most queries) are asked for 0% positive: this file is about the filters.
MIX = "positive_pct=0&limit=50"


def titles(response):
    return [a["title"] for a in response.json()["articles"]]


def test_search_ignores_accents_and_case(client):
    for query in ["incendio", "INCÊNDIO", "Incendio"]:
        found = titles(client.get(f"/api/feed?q={query}&{MIX}"))
        assert len(found) == 2, query
        assert all("ncêndio" in t for t in found)


def test_search_requires_every_word(client):
    assert len(titles(client.get(f"/api/feed?q=incendio sintra&{MIX}"))) == 1
    assert titles(client.get(f"/api/feed?q=incendio benfica&{MIX}")) == []


def test_search_matches_the_summary_too(client):
    client_response = client.get(f"/api/feed?q=evacuacao&{MIX}")
    assert len(titles(client_response)) == 1


def test_blank_search_returns_everything(client):
    """A blank query filters nothing: same result as not searching at all."""
    assert titles(client.get(f"/api/feed?q=   &{MIX}")) == titles(client.get(f"/api/feed?{MIX}"))


def test_search_respects_scope_and_outlet_filters(client):
    assert titles(client.get(f"/api/feed?q=wildfire&{MIX}")) == []            # portugal scope
    assert len(titles(client.get(f"/api/feed?q=wildfire&scope=world&{MIX}"))) == 1
    assert len(titles(client.get(f"/api/feed?q=incendio&sources=rtp&{MIX}"))) == 1


def test_search_term_is_echoed_back(client):
    assert client.get("/api/feed?q=  incendio ").json()["q"] == "incendio"


def test_unmatched_search_returns_nothing(client):
    assert titles(client.get(f"/api/feed?q=girafas&{MIX}")) == []


def test_topics_endpoint_returns_at_most_six(client):
    topics.recompute_all(main.conn)
    assert len(client.get("/api/topics?scope=portugal").json()) <= 6
