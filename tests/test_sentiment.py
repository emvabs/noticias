from app.ingest import normalize_title, parse_feed
from app.sentiment import Scorer, get_scorer, load_vader, normalize, tokenize


def make_scorer():
    return Scorer(base={"bom": 1, "livro": -1},
                  custom_exact={"morte": -1, "vitoria": 1, "livro": 0},
                  custom_prefixes=[("incendi", -1)])


def test_normalize_strips_accents_and_case():
    assert normalize("Incêndio em São João") == "incendio em sao joao"
    assert tokenize("Vitória! Não-há 2 mortos.") == ["vitoria", "nao-ha", "2", "mortos"]


def test_score_formula_and_labels():
    s = make_scorer()
    score, label, _ = s.score("Vitória e bom resultado")       # 2 pos, 0 neg -> 2/3
    assert round(score, 2) == 0.67 and label == "positive"
    score, label, _ = s.score("Morte após incêndios")          # 0 pos, 2 neg
    assert round(score, 2) == -0.67 and label == "negative"
    score, label, _ = s.score("Vitória marcada por morte")     # 1 pos, 1 neg -> 0
    assert score == 0 and label == "neutral"
    assert s.score("Sem palavras relevantes")[1] == "neutral"


def test_custom_overrides_and_ignores_base_lexicon():
    _, _, matched = make_scorer().score("Um livro bom")
    assert [m["word"] for m in matched] == ["bom"]


def test_negation_within_three_words():
    s = make_scorer()
    score, label, matched = s.score("Não foi uma vitória")      # "nao" 3 words before
    assert matched[0]["negated"] and label == "negative"
    _, _, matched = s.score("Não foi mesmo uma grande vitória")  # 5 words before -> no flip
    assert not matched[0]["negated"]
    assert s.score("Acidente sem morte")[1] == "positive"        # "sem" flips morte


def test_normalize_title_for_dedupe():
    assert normalize_title("Guerra: ataque em Kiev!") == normalize_title("guerra ataque em kiev")


def test_parse_feed_cleans_html():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Vit&#243;ria hist&#243;rica</title><link>https://x.pt/a</link>
    <description>&lt;img src="a.jpg"/&gt; &lt;p&gt;Texto &amp;amp; mais&lt;/p&gt;</description>
    <pubDate>Mon, 21 Sep 2026 17:05:42 +0100</pubDate></item></channel></rss>"""
    from datetime import datetime, timezone
    items = parse_feed(rss, "x", datetime(2026, 9, 22, tzinfo=timezone.utc))
    assert items[0]["title"] == "Vitória histórica"
    assert items[0]["summary"] == "Texto & mais"
    assert items[0]["published_at"] == "2026-09-21T16:05:42Z"


def test_english_scorer_uses_its_own_lexicon_and_negation():
    en = get_scorer("en")
    assert en.score("Air strike kills 12 civilians")[1] == "negative"
    assert en.score("Ceasefire holds as hostages are freed")[1] == "positive"
    # "not" flips the following word; contractions lose the apostrophe first
    assert en.score("Talks were not successful")[2][0]["negated"]
    assert en.score("Rescue didnt succeed")[2][-1]["negated"]
    assert normalize("doesn\u2019t") == "doesnt"


def test_languages_are_scored_separately():
    pt = get_scorer("pt")
    # "vitoria" is Portuguese-only; "kills" is English-only
    assert pt.score("Vitória histórica")[1] == "positive"
    assert pt.polarity("kills") == (0, None)
    assert get_scorer("en").polarity("vitoria") == (0, None)


def test_load_vader_skips_emoticons_and_weak_words(tmp_path):
    f = tmp_path / "v.txt"
    f.write_text("killed\t-2.9\t0.5\t[]\n:-)\t2.0\t0.5\t[]\nmeh\t-0.3\t0.5\t[]\n", encoding="utf-8")
    assert load_vader(f) == {"killed": -1}


def test_clean_text_handles_double_escaped_html():
    from app.ingest import clean_text
    assert clean_text("&lt;p&gt;Ata&amp;#xe7;&amp;#xe3;o &amp;amp; mais&lt;/p&gt;") == "Atação & mais"
    assert clean_text("<p>Texto &amp; mais</p>") == "Texto & mais"


# ---------- combination rules ----------

def rule_scorer():
    """A scorer where 'ultrapassa' is positive on its own, as SentiLex has it."""
    return Scorer(base={"ultrapassa": 1, "desemprego": -1, "desce": -1},
                  rules=[{"terms": ["euribor", "ultrapassa"], "weight": -1,
                          "label": "euribor + ultrapassa"},
                         {"terms": ["desemprego", "desce"], "weight": 1,
                          "label": "desemprego + desce"}])


def test_rule_overrides_the_words_it_names():
    score, label, matched = rule_scorer().score("Taxa Euribor ultrapassa 3% pela primeira vez")
    assert label == "negative"
    assert [m["word"] for m in matched] == ["euribor + ultrapassa"]   # "ultrapassa+" is gone
    assert matched[0]["source"] == "rule"


def test_rule_can_turn_two_negatives_into_good_news():
    score, label, matched = rule_scorer().score("Taxa de desemprego desce para mínimo")
    assert label == "positive"
    assert [m["word"] for m in matched] == ["desemprego + desce"]


def test_rule_needs_every_word_present():
    _, label, matched = rule_scorer().score("Empresa ultrapassa mil milhões em vendas")
    assert label == "positive"                      # no "euribor", so the word counts
    assert [m["word"] for m in matched] == ["ultrapassa"]


def test_rule_words_may_come_in_any_order():
    assert rule_scorer().score("Desce o desemprego em Portugal")[1] == "positive"


def test_rule_terms_support_prefixes():
    scorer = Scorer(base={}, rules=[{"terms": ["euribor", "sub*"], "weight": -1,
                                     "label": "euribor + sub*"}])
    assert scorer.score("Euribor subiu de novo")[1] == "negative"
    assert scorer.score("Euribor estável este mês")[1] == "neutral"


def test_rules_load_from_file(tmp_path):
    from app.sentiment import load_rules
    path = tmp_path / "rules.txt"
    path.write_text("# comentário\neuribor, sobe -1\ndesemprego, desce 1\nlinha inválida\n",
                    encoding="utf-8")
    rules = load_rules(path)
    assert [r["terms"] for r in rules] == [["euribor", "sobe"], ["desemprego", "desce"]]
    assert [r["weight"] for r in rules] == [-1, 1]


def test_real_rules_fix_the_euribor_headline():
    """The headline that started this: a rate rising is not good news."""
    _, label, _ = get_scorer("pt").score(
        "Taxa Euribor a 6 meses ultrapassa 3% pela primeira vez desde outubro de 2024")
    assert label == "negative"


def test_contradictory_rules_cancel_each_other():
    """"sobem os preços, descem os consumos" fires both directions: ambiguous."""
    scorer = Scorer(base={}, rules=[
        {"terms": ["precos", "sobem"], "weight": -1, "label": "precos + sobem"},
        {"terms": ["precos", "descem"], "weight": 1, "label": "precos + descem"}])
    score, label, matched = scorer.score("Combustíveis: sobem os preços, descem os consumos")
    assert label == "neutral" and matched == []


def test_negation_words_are_not_sentiment_themselves():
    """VADER scores "no" negative; as a negation word it must not count twice."""
    scorer = Scorer(base={"no": -1, "deal": 1}, negation_words=["no", "not"])
    score, label, matched = scorer.score("Deal reached")
    assert [m["word"] for m in matched] == ["deal"] and label == "positive"
    # as an operator it still flips what follows
    _, label, matched = scorer.score("No deal reached")
    assert [m["word"] for m in matched] == ["deal"] and matched[0]["negated"]
    assert label == "negative"


def test_outlet_names_are_not_sentiment():
    """SentiLex has "observador" as positive; the byline must not tint the score."""
    assert get_scorer("pt").score("Observador explica o que mudou nas notícias")[1] == "neutral"
    assert get_scorer("pt").polarity("observador") == (0, "custom")
