from app import db, ingest, topics


def rows(*texts, start_id=1):
    """Fake article rows: ("title", "summary") or just "title"."""
    out = []
    for i, t in enumerate(texts):
        title, summary = t if isinstance(t, tuple) else (t, "")
        out.append({"id": start_id + i, "title": title, "summary": summary})
    return out


STOP = topics._normalized(topics.STOPWORDS_PT | topics.STOPWORDS_EN)


def slugs(found):
    return [t["slug"] for t in found]


def test_terms_include_unigrams_bigrams_and_proper_phrases():
    terms = topics.candidate_terms("O governador visitou o Banco de Portugal esta semana", STOP)
    assert "banco de portugal" in terms
    assert terms["banco de portugal"][0] == "Banco de Portugal"   # display label keeps case
    assert "governador" in terms
    assert "de" not in terms                                       # stopword


def test_sentence_initial_capital_is_not_a_proper_noun():
    terms = topics.candidate_terms("Casa ardeu. Havia pessoas dentro", STOP)
    assert not any(kind == "proper" for _, kind, _pos in terms.values())


def test_tfidf_favours_what_is_unusual_now():
    window = rows(*["Greve dos comboios afeta o país"] * 4,
                  *["Reunião do conselho de ministros"] * 3)
    # "reunião do conselho" happens every week; the strike does not
    background = rows(*["Reunião do conselho de ministros"] * 40, start_id=100)
    found = topics.extract(window, background, stopwords=STOP, min_articles=3)
    assert "greve" in slugs(found)
    assert slugs(found)[0] == "greve"


def test_singular_and_plural_are_merged():
    window = rows(*["Incêndio em Sintra obriga a evacuação"] * 3,
                  *["Incêndios no norte do país"] * 4)
    found = topics.extract(window, [], stopwords=STOP, min_articles=3)
    assert slugs(found).count("incendios") + slugs(found).count("incendio") == 1


def test_overlapping_terms_are_merged_without_inflating_the_count():
    # Every word of the first headline co-occurs in the same 5 articles, so the
    # cluster must collapse to a single topic whose count is those 5 articles -
    # not the union of everything that merged into it.
    window = rows(*["Habitação jovem com nova lei aprovada"] * 5,
                  *["Ministro fala de saúde"] * 4)
    found = topics.extract(window, [], stopwords=STOP, min_articles=3)
    from_cluster = [t for t in found if t["count"] == 5]
    assert len(from_cluster) == 1
    assert from_cluster[0]["slug"] == "habitacao"      # the word the headline leads with
    assert all(t["count"] <= 5 for t in found)


def test_rare_terms_below_the_threshold_are_dropped():
    window = rows("Notícia sobre girafas", *["Tema repetido sobre eleições"] * 3)
    found = topics.extract(window, [], stopwords=STOP, min_articles=3)
    assert "girafas" not in slugs(found)


def test_empty_window_gives_no_topics():
    assert topics.extract([], rows("Qualquer coisa"), stopwords=STOP) == []


def test_compute_for_scope_stores_cache_and_tags_articles():
    conn = db.connect(":memory:")
    now = ingest.utcnow()
    articles = []
    for i in range(4):
        articles.append({"source": "x", "title": f"Greve dos comboios afeta o país ({i})",
                         "title_norm": f"greve {i}", "summary": "", "url": f"https://a/{i}",
                         "scope": "portugal", "language": "pt",
                         "published_at": ingest.iso(now), "fetched_at": ingest.iso(now)})
    ingest.store(conn, articles)
    found = topics.compute_for_scope(conn, "portugal", now=now)

    assert "greve" in slugs(found)
    cached = topics.current(conn, "portugal")
    assert cached[0]["slug"] == slugs(found)[0] and cached[0]["count"] >= 3
    tagged = conn.execute("SELECT topics FROM articles").fetchall()
    assert all("greve" in row["topics"] for row in tagged)


def test_outlet_names_are_not_topics():
    assert "cnn" in topics.outlet_words()
    assert "observador" in topics.outlet_words()
