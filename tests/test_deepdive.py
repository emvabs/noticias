"""Deep Dive findings, silences and their API."""
import pytest
from fastapi.testclient import TestClient

from app import archive, db, deepdive, ingest, main, ownership

CFG = {**deepdive.settings(), "min_articles": 5, "topic_min_articles": 3,
       "silence_min_outlet_articles": 5, "silence_min_expected": 1}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(deepdive, "settings", lambda: CFG)
    return db.connect(":memory:")


def add(conn, n, source, title, label="neutral", authors=None, topics="[]", scope="portugal",
        group="mainstream"):
    now = ingest.iso(ingest.utcnow())
    ingest.store(conn, [{"source": source, "title": title, "title_norm": f"t{n} {title}",
                         "summary": "", "url": f"https://x/{n}", "scope": scope, "group": group,
                         "published_at": now, "fetched_at": now, "authors": authors}])
    conn.execute("UPDATE articles SET label = ?, topics = ? WHERE url = ?",
                 (label, topics, f"https://x/{n}"))


def fill(conn):
    """Público: 10 pieces, 8 negative and 6 about Benfica. Others: 30 mostly neutral."""
    n = 0
    for i in range(10):
        n += 1
        add(conn, n, "publico", f"Benfica ganha jogo {i}" if i < 6 else f"Chuva forte {i}",
            "negative" if i < 8 else "neutral", authors=["Agência Lusa"] if i < 3 else ["Ana Sousa"])
    for src in ("expresso", "observador", "cnn"):
        for i in range(10):
            n += 1
            add(conn, n, src, f"Governo aprova medida {src} {i}",
                "negative" if i < 2 else "neutral", authors=["Rui Costa"])
    archive.sync(conn)


def kinds(result):
    return [f["kind"] for f in result["findings"]]


def test_tone_finding_compares_with_the_other_outlets(conn):
    fill(conn)
    r = deepdive.dive(conn, "outlet", "publico")
    tone = next(f for f in r["findings"] if f["kind"] == "tone")
    assert tone["title"] == "Mais notícias negativas que os outros jornais portugueses"
    assert tone["numbers"]["share"]["negative"] == 80 and tone["numbers"]["baseline"]["negative"] == 20
    assert tone["total"] == 8 and all(a["label"] == "negative" for a in tone["articles"])


def test_less_negative_is_a_finding_too(conn):
    fill(conn)   # Expresso 20% negative, the others 40%
    tone = next(f for f in deepdive.dive(conn, "outlet", "expresso")["findings"] if f["kind"] == "tone")
    assert tone["title"] == "Menos notícias negativas que os outros jornais portugueses"
    assert all(a["label"] != "negative" for a in tone["articles"])


def test_no_tone_finding_below_the_threshold(conn):
    n = 0
    for src in ("publico", "expresso", "observador"):
        for i in range(10):
            n += 1
            add(conn, n, src, f"Notícia {src} {i}", "negative" if i < 3 else "neutral")
    archive.sync(conn)
    r = deepdive.dive(conn, "outlet", "publico")
    assert "tone" not in kinds(r)
    assert "em linha com a média" in r["summary"]


def test_topic_finding_lists_the_headlines_behind_it(conn):
    fill(conn)
    topic = next(f for f in deepdive.dive(conn, "outlet", "publico")["findings"]
                 if f["kind"] == "topic" and "Benfica" in f["title"])
    assert topic["total"] == 6
    assert all("Benfica" in a["title"] for a in topic["articles"])
    assert topic["numbers"]["in_baseline"] == 0


def test_agency_share_counts_only_known_bylines(conn):
    fill(conn)
    agency = next(f for f in deepdive.dive(conn, "outlet", "publico")["findings"]
                  if f["kind"] == "agency")
    assert agency["numbers"]["share"] == 30 and agency["numbers"]["baseline"] == 0
    assert "Agência Lusa" in agency["text"]


def test_owner_finding(conn, monkeypatch):
    monkeypatch.setattr(ownership, "keywords_by_source", lambda: {"publico": ["Benfica"]})
    fill(conn)
    owner = next(f for f in deepdive.dive(conn, "outlet", "publico")["findings"]
                 if f["kind"] == "owner")
    assert owner["total"] == 6


def test_few_articles_give_no_data_findings(conn):
    add(conn, 1, "publico", "Uma notícia", authors=["Ana Sousa"])
    archive.sync(conn)
    r = deepdive.dive(conn, "journalist", "ana-sousa")
    assert not r["enough_data"] and r["articles"] == 1
    assert "poucas para tirar tendências" in r["summary"]
    assert all(f["kind"] == "researched" for f in r["findings"])


def test_journalist_is_compared_with_the_rest_of_their_outlet(conn):
    fill(conn)
    r = deepdive.dive(conn, "journalist", "rui-costa")
    assert r["outlets"] == ["Expresso", "Observador", "CNN Portugal"]
    tone = [f for f in r["findings"] if f["kind"] == "tone"]
    # Rui Costa writes all of those outlets' pieces: nothing left to compare with
    assert tone == []


def test_researched_findings_need_a_source():
    profile = {"owners": [{"name": "X", "share": 100, "kind": "company"}],   # no source
               "narrative": [{"text": "Crítica", "source": "https://a"}]}
    found = deepdive.researched_findings(profile, "outlet")
    assert [f["title"] for f in found] == ["Narrativa e críticas documentadas"]
    assert found[0]["sources"] == ["https://a"]


def test_every_finding_has_evidence(conn):
    fill(conn)
    for outlet in ("publico", "expresso", "cnn", "lusa"):
        for f in deepdive.dive(conn, "outlet", outlet)["findings"]:
            assert f.get("sources") or f.get("total"), f


def test_silences_mark_an_outlet_that_skipped_a_widely_covered_topic(conn):
    n = 0
    for src in ("publico", "expresso", "observador", "cnn"):
        for i in range(10):
            n += 1
            topics = '["irao"]' if src != "cnn" and i < 4 else "[]"
            add(conn, n, src, f"Notícia {src} {i}", topics=topics)
    conn.execute("""INSERT INTO topics_cache (scope, "group", slug, label, count, rank, computed_at)
                    VALUES ('portugal', 'mainstream', 'irao', 'Irão', 12, 0, 'x')""")
    archive.sync(conn)
    table = deepdive.silences(conn, "portugal")
    irao = next(t for t in table["topics"] if t["slug"] == "irao")
    assert irao["label"] == "Irão" and irao["covered_by"] == 3
    assert irao["cells"]["cnn"]["silent"] and not irao["cells"]["publico"]["silent"]
    finding = next(f for f in deepdive.dive(conn, "outlet", "cnn")["findings"] if f["kind"] == "silence")
    assert finding["title"] == "Pouca cobertura de «Irão»"
    assert finding["total"] == 12 and all(a["source"] != "cnn" for a in finding["articles"])
    assert len(deepdive.silence_articles(conn, "portugal", "irao", "publico")) == 4


def test_numbers_are_written_the_portuguese_way():
    assert deepdive._num(37.05) == "37,05" and deepdive._num(5.0) == "5"


@pytest.fixture
def client(conn, monkeypatch):
    fill(conn)
    monkeypatch.setattr(main, "conn", conn)
    return TestClient(main.app)


def test_api_index_and_entity(client):
    outlets = client.get("/api/deepdive/index?kind=outlet").json()
    assert outlets[0]["articles"] >= outlets[-1]["articles"]
    assert any(o["id"] == "lusa" and o["agency"] for o in outlets)
    people = client.get("/api/deepdive/index?kind=journalist").json()
    assert [p["id"] for p in people[:2]] == ["rui-costa", "ana-sousa"]
    r = client.get("/api/deepdive/outlet/publico").json()
    assert r["name"] == "Público" and r["findings"]
    assert client.get("/api/deepdive/journalist/ninguem-mesmo").status_code == 404
    assert client.get("/api/deepdive/outlet/..%2Fx").status_code == 404
    assert client.get("/api/deepdive/index?kind=nada").status_code == 422


def test_api_ownership_and_silences(client):
    g = client.get("/api/ownership").json()
    assert {"owners", "outlets", "links"} <= set(g)
    assert next(o for o in g["outlets"] if o["id"] == "reuters")["agency"] is True
    assert client.get("/api/silences?scope=portugal").json()["scope"] == "portugal"


def test_silences_leave_independent_outlets_out(conn):
    for i in range(10):
        add(conn, i, "oregioes", f"Notícia regional {i}", group="independent")
    archive.sync(conn)
    assert all(o["id"] != "oregioes" for o in deepdive.silences(conn, "portugal")["outlets"])


def silent_cnn(conn):
    """CNN skips «Irão», which the other three outlets cover (as in the test above)."""
    n = 0
    for src in ("publico", "expresso", "observador", "cnn"):
        for i in range(10):
            n += 1
            topics = '["irao"]' if src != "cnn" and i < 4 else "[]"
            add(conn, n, src, f"Notícia {src} {i}", topics=topics,
                label="positive" if i == 9 else "negative" if i < 3 else "neutral")
    conn.execute("""INSERT INTO topics_cache (scope, "group", slug, label, count, rank, computed_at)
                    VALUES ('portugal', 'mainstream', 'irao', 'Irão', 12, 0, 'x')""")
    archive.sync(conn)


def test_strongest_silence_is_the_biggest_gap(conn):
    silent_cnn(conn)
    best = deepdive.strongest_silence(deepdive.silences(conn, "portugal"))
    assert best["outlet"] == "cnn" and best["topic"] == "irao" and best["count"] == 0
    # only topics trending now count
    assert deepdive.strongest_silence(deepdive.silences(conn, "portugal"), {"outro"}) is None


def test_api_today_counts_the_last_day_and_names_the_silence(conn, monkeypatch):
    silent_cnn(conn)
    old = ingest.iso(ingest.utcnow() - __import__("datetime").timedelta(hours=30))
    conn.execute("UPDATE articles SET published_at = ? WHERE source = 'publico'", (old,))
    monkeypatch.setattr(main, "conn", conn)
    data = TestClient(main.app).get("/api/today?scope=portugal").json()
    assert data["total"] == 30                    # Público's ten are older than a day
    assert data["labels"] == {"positive": 3, "neutral": 18, "negative": 9}
    assert data["silence"]["outlet"] == "cnn"
    independent = TestClient(main.app).get("/api/today?scope=portugal&group=independent").json()
    assert independent["silence"] is None and independent["total"] == 0


def test_tone_trend_has_gaps_where_data_is_thin(conn):
    from datetime import timedelta
    now = ingest.utcnow()
    n = 0
    for week, count in ((0, 6), (3, 2)):          # this week: 6 articles; three weeks ago: 2
        for i in range(count):
            n += 1
            add(conn, n, "publico", f"Peça {week} {i}", label="negative" if i < 3 else "positive")
            stamp = ingest.iso(now - timedelta(days=7 * week + 1))
            conn.execute("UPDATE articles SET published_at = ? WHERE url = ?", (stamp, f"https://x/{n}"))
    archive.sync(conn)
    t = deepdive.tone_trend(conn, "outlet", "publico", weeks=6, min_articles=5, now=now)
    last = t["points"][-1]
    assert last["articles"] == 6 and last["negative_pct"] == 50 and last["positive_pct"] == 50
    assert t["points"][-4]["articles"] == 2 and t["points"][-4]["positive_pct"] is None
    assert t["points"][0]["articles"] == 0 and t["points"][0]["negative_pct"] is None
    assert deepdive.tone_trend(conn, "scope", "portugal", weeks=6, min_articles=5, now=now)["points"][-1]["articles"] == 6


def test_api_trends(client):
    data = client.get("/api/trends?kind=outlet&id=publico").json()
    assert data["weeks"] == len(data["points"]) == 12
    assert client.get("/api/trends?kind=nada&id=publico").status_code == 422
    assert client.get("/api/trends?kind=outlet&id=..%2Fx").status_code == 404
