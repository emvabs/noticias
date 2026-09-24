"""Search matching: whole words, acronyms and synonyms."""
import pytest

from app import db, ingest, search


@pytest.fixture
def conn(tmp_path, monkeypatch):
    conn = db.connect(":memory:")
    now = ingest.utcnow()
    for i, (title, summary) in enumerate([
        ("IA oferece soluções de eficiência energética", ""),
        ("O mito fundador", "Quando a inteligência artificial guia a história"),
        ("ChatGPT chega às escolas", ""),
        ("Aí vem o outono: mais chuva a partir de terça", "O tempo muda aí para o fim da semana"),
        ("Verão na praia do Guincho bate recordes", ""),
        ("Incêndios no norte obrigam a evacuação", ""),
        ("Incêndio destrói casa em Sintra", ""),
        ("PJ detém suspeito de rapto", ""),
        ("Bruxelas aprova novo pacote", ""),
    ]):
        ingest.store(conn, [{
            "source": "x", "title": title, "title_norm": f"t{i}", "summary": summary,
            "url": f"https://x/{i}", "scope": "portugal", "language": "pt",
            "published_at": ingest.iso(now), "fetched_at": ingest.iso(now)}])
    return conn


def find(conn, query):
    sql, params = search.sql_clause(query)
    if not sql:
        return [r["title"] for r in conn.execute("SELECT title FROM articles")]
    return [r["title"] for r in conn.execute(f"SELECT title FROM articles WHERE {sql}", params)]


def test_search_matches_whole_words_not_fragments(conn):
    found = find(conn, "IA")
    assert not any("praia" in t.lower() for t in found)      # "praia" contains "ia"
    assert not any("guia" in t.lower() for t in found)


def test_acronym_does_not_match_the_word_ai(conn):
    """Stripping accents turns 'aí' into 'ai'; the acronym must not match it."""
    assert not any(t.startswith("Aí vem") for t in find(conn, "IA"))
    assert not any(t.startswith("Aí vem") for t in find(conn, "AI"))


def test_acronym_is_found_whatever_the_user_types(conn):
    for typed in ["IA", "ia", "Ia", "AI"]:
        assert "IA oferece soluções de eficiência energética" in find(conn, typed), typed


def test_synonyms_work_in_both_directions(conn):
    by_acronym = find(conn, "IA")
    assert "O mito fundador" in by_acronym            # "inteligência artificial"
    assert "ChatGPT chega às escolas" in by_acronym
    assert "IA oferece soluções de eficiência energética" in find(conn, "inteligência artificial")


def test_synonym_group_beyond_acronyms(conn):
    assert "Bruxelas aprova novo pacote" in find(conn, "UE")


def test_long_words_still_match_plurals(conn):
    found = find(conn, "incendio")
    assert len(found) == 2                             # incêndio and incêndios


def test_multi_word_query_requires_every_word(conn):
    assert find(conn, "incendio sintra") == ["Incêndio destrói casa em Sintra"]
    assert find(conn, "incendio lisboa") == []


def test_blank_query_matches_everything(conn):
    assert len(find(conn, "   ")) == 9


def test_expansion_is_reported_for_the_page(conn):
    """The page shows the spelling from synonyms.txt, accents and all."""
    assert "inteligência artificial" in search.expand("IA")
    assert search.expand("incendio") == []


def test_searchable_pads_and_strips_punctuation():
    assert search.searchable('IA: a "nova" era!') == " ia a nova era "
    assert search.raw_searchable('IA: a "nova" era — aí!') == " IA a nova era aí "
