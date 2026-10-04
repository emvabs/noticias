"""Story clustering: the same story from several outlets becomes one item."""
from datetime import timedelta

from app import clusters, db, ingest


def rows(*items, start_id=1):
    """Fake article rows from (title, summary) pairs."""
    return [{"id": start_id + i, "title": t, "summary": s} for i, (t, s) in enumerate(items)]


FIRE = [
    ("Incêndio em Leiria obriga a evacuar três aldeias",
     "Mais de 200 bombeiros combatem as chamas no concelho de Leiria; há feridos ligeiros."),
    ("Fogo em Leiria: três aldeias evacuadas durante a noite",
     "As chamas obrigaram a retirar moradores; os bombeiros continuam no terreno."),
    ("Bombeiros combatem incêndio em Leiria com meios aéreos",
     "O fogo deflagrou ao fim da tarde e já obrigou à evacuação de aldeias."),
]
STRIKE = [
    ("Greve dos professores fecha escolas em Lisboa", "Sindicatos exigem recuperação do tempo de serviço."),
    ("Professores em greve: escolas encerradas em Lisboa e no Porto",
     "Paralisação convocada pelos sindicatos afeta milhares de alunos."),
]
RENTS = [("Rendas em Lisboa sobem 12% num ano", "A crise da habitação agrava-se na capital.")]


def test_same_story_in_other_words_is_one_cluster():
    found = clusters.group(rows(*FIRE, *STRIKE, *RENTS))
    assert sorted(found[0]) == [1, 2, 3]
    assert sorted(found[1]) == [4, 5]


def test_sharing_a_place_name_is_not_the_same_story():
    """The strike and the rents both happen in Lisboa; that alone does not join them."""
    found = clusters.group(rows(*STRIKE[:1], *RENTS))
    assert sorted(len(c) for c in found) == [1, 1]


def test_one_shared_word_is_not_enough():
    """Headline formulas share a word ("sabe") and nothing else."""
    found = clusters.group(rows(("O que se sabe sobre o incêndio", ""), ("O que se sabe sobre a greve", ""),
                                ("O que se sabe sobre isto", "")))
    assert all(len(c) == 1 for c in found)


def test_cluster_size_is_capped():
    same = [("Benfica vence Sporting no dérbi da Luz", "Golos do Benfica na segunda parte.")] * 6
    found = clusters.group(rows(*same), max_size=4)
    assert max(len(c) for c in found) == 4
    assert sum(len(c) for c in found) == 6


def test_a_weak_chain_does_not_merge_two_stories():
    """A~B and B~C, but A and C have nothing in common: the groups stay apart
    when their average similarity is below min_group_similarity."""
    a = ("Benfica vence Sporting no dérbi", "Golos na Luz.")
    b = ("Benfica vence dérbi e Governo aprova orçamento", "Dia cheio.")
    c = ("Governo aprova orçamento do Estado", "Conselho de Ministros.")
    loose = clusters.group(rows(a, b, c), min_similarity=0.2, min_group_similarity=0.0)
    strict = clusters.group(rows(a, b, c), min_similarity=0.2, min_group_similarity=0.3)
    assert max(len(g) for g in loose) == 3
    assert max(len(g) for g in strict) < 3


def store(conn, title, summary, source, scope="portugal", hours_ago=1):
    now = ingest.utcnow()
    ingest.store(conn, [{
        "source": source, "title": title, "title_norm": ingest.normalize_title(title),
        "summary": summary, "url": f"https://x/{source}/{abs(hash(title))}", "scope": scope,
        "language": "pt" if scope == "portugal" else "en",
        "published_at": ingest.iso(now - timedelta(hours=hours_ago)), "fetched_at": ingest.iso(now),
    }])
    return conn.execute("SELECT id FROM articles WHERE title = ?", (title,)).fetchone()[0]


def test_cluster_ids_are_stored_per_scope_and_window():
    conn = db.connect(":memory:")
    a = store(conn, *FIRE[0], "publico")
    b = store(conn, *FIRE[1], "rtp")
    c = store(conn, *FIRE[2], "expresso", hours_ago=24 * 5)     # outside the window
    d = store(conn, "Fire in Leiria: three villages evacuated overnight", FIRE[1][1], "bbc",
              scope="world")                                       # other scope
    clusters.recompute_all(conn)
    cid = dict(conn.execute("SELECT id, cluster_id FROM articles").fetchall())
    assert cid[a] == cid[b] == min(a, b)
    assert cid[c] == c and cid[d] == d


def test_migration_adds_the_column(tmp_path):
    path = tmp_path / "old.db"
    import sqlite3
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)          # the original table, without cluster_id
    old.commit()
    old.close()
    conn = db.connect(path)
    assert "cluster_id" in {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
