"""Story clustering: the same story told by several outlets becomes one item.

Within a scope's recent window, each article becomes a weighted bag of its
meaningful words (stopwords, outlet names and config/stoplist.txt removed,
plurals merged, title words counted double, proper nouns a little more),
scored by TF-IDF across the window. Two articles are linked when the cosine
similarity of their bags reaches `clusters.min_similarity` and they share at
least two terms (one shared word is a headline formula, not a story).

Links are merged strongest first, and two groups only merge when the average
similarity between all their members still reaches `min_group_similarity`
and the result stays under `max_size`: a single vague word shared by two
headlines cannot chain unrelated stories into one.

Each article's `cluster_id` is the smallest id in its group (its own id when
it stands alone). Run `python -m app.clusters` to print the current clusters.
"""
import math
from datetime import timedelta

from . import config
from .sentiment import normalize
from .topics import (STOPWORDS_EN, STOPWORDS_PT, WORD_RE, is_capitalized,
                     load_stoplist, outlet_words, singular)

DEFAULTS = {
    "window_hours": 48,             # only recent articles are compared
    "min_similarity": 0.35,         # cosine similarity for two articles to be linked
    "min_group_similarity": 0.2,    # average similarity for two groups to merge
    "max_size": 12,                 # no story group grows past this
}
TITLE_WEIGHT = 2.0      # the headline says what the story is; the summary adds detail
PROPER_WEIGHT = 1.5     # "Leiria", "Benfica": names tie two versions of a story together


def settings():
    cfg = config.load_config().get("clusters") or {}
    return {key: cfg.get(key, default) for key, default in DEFAULTS.items()}


def stopwords():
    return ({normalize(w) for w in STOPWORDS_PT | STOPWORDS_EN}
            | load_stoplist() | outlet_words())


def terms(title, summary, stop):
    """A weighted bag of words: {term: weight}."""
    bag = {}
    for text, weight in ((title, TITLE_WEIGHT), (summary, 1.0)):
        for i, word in enumerate(WORD_RE.findall(text or "")):
            low = normalize(word)
            if len(low) < 3 or low in stop:
                continue
            # The first word of a sentence-case headline is capitalized anyway.
            boost = PROPER_WEIGHT if i > 0 and is_capitalized(word) else 1.0
            key = singular(low)
            bag[key] = bag.get(key, 0.0) + weight * boost
    return bag


def vectors(rows, stop):
    """Unit TF-IDF vectors, one per row, with the IDF taken over these rows."""
    bags = [terms(r["title"], r["summary"], stop) for r in rows]
    df = {}
    for bag in bags:
        for term in bag:
            df[term] = df.get(term, 0) + 1
    n = len(bags)
    out = []
    for bag in bags:
        vec = {t: w * (math.log((n + 1) / (df[t] + 1)) + 1) for t, w in bag.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        out.append({t: v / norm for t, v in vec.items()})
    return out


MIN_SHARED = 2   # one shared word ("sabe" in "O que se sabe sobre…") is a formula, not a story


def similarities(vecs):
    """{(i, j): cosine} for every pair (i < j) sharing at least MIN_SHARED terms."""
    index = {}
    for i, vec in enumerate(vecs):
        for term in vec:
            index.setdefault(term, []).append(i)
    sims = {}
    for i, vec in enumerate(vecs):
        acc = {}
        shared = {}
        for term, w in vec.items():
            for j in index[term]:
                if j > i:
                    acc[j] = acc.get(j, 0.0) + w * vecs[j][term]
                    shared[j] = shared.get(j, 0) + 1
        for j, s in acc.items():
            if shared[j] >= MIN_SHARED:
                sims[(i, j)] = s
    return sims


def group(rows, stop=None, min_similarity=None, min_group_similarity=None, max_size=None):
    """Cluster rows ({id, title, summary}); returns lists of ids, biggest first."""
    cfg = settings()
    min_similarity = cfg["min_similarity"] if min_similarity is None else min_similarity
    min_group_similarity = (cfg["min_group_similarity"] if min_group_similarity is None
                            else min_group_similarity)
    max_size = cfg["max_size"] if max_size is None else max_size
    stop = stopwords() if stop is None else stop

    sims = similarities(vectors(rows, stop))
    parent = list(range(len(rows)))
    members = {i: [i] for i in range(len(rows))}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def sim(x, y):
        return sims.get((x, y) if x < y else (y, x), 0.0)

    # Strongest links first, so a weak link never decides what a strong one would not.
    for s, i, j in sorted(((s, i, j) for (i, j), s in sims.items() if s >= min_similarity),
                          reverse=True):
        a, b = find(i), find(j)
        if a == b:
            continue
        group_a, group_b = members[a], members[b]
        if len(group_a) + len(group_b) > max_size:
            continue
        average = sum(sim(x, y) for x in group_a for y in group_b) / (len(group_a) * len(group_b))
        if average < min_group_similarity:
            continue
        parent[b] = a
        members[a] = group_a + group_b
        del members[b]

    found = [[rows[i]["id"] for i in idx] for idx in members.values()]
    return sorted(found, key=lambda ids: (-len(ids), min(ids)))


# ---------- database side ----------

def compute_for_scope(conn, scope, now=None):
    """Cluster one scope's recent articles and store each one's cluster_id."""
    from .ingest import iso, utcnow
    now = now or utcnow()
    cfg = settings()
    start = iso(now - timedelta(hours=cfg["window_hours"]))
    rows = conn.execute(
        "SELECT id, title, summary FROM articles WHERE scope = ? AND published_at >= ?",
        (scope, start)).fetchall()
    found = group([dict(r) for r in rows], min_similarity=cfg["min_similarity"],
                  min_group_similarity=cfg["min_group_similarity"], max_size=cfg["max_size"])
    # Everything stands alone until shown otherwise, older articles included.
    conn.execute("UPDATE articles SET cluster_id = id WHERE scope = ?", (scope,))
    conn.executemany("UPDATE articles SET cluster_id = ? WHERE id = ?",
                     [(min(ids), i) for ids in found if len(ids) > 1 for i in ids])
    conn.commit()
    return [ids for ids in found if len(ids) > 1]


def recompute_all(conn, now=None):
    scopes = [r[0] for r in conn.execute("SELECT DISTINCT scope FROM articles")]
    return {scope: compute_for_scope(conn, scope, now) for scope in scopes}


def main():
    """CLI: print the stories told by more than one article, per scope."""
    from . import db
    conn = db.connect()
    names = {s["id"]: s["name"] for s in config.load_sources(enabled_only=False)}
    found = recompute_all(conn)
    for scope, clusters in found.items():
        print(f"\n=== {scope}: {len(clusters)} histórias com mais de uma notícia ===")
        for ids in clusters:
            rows = conn.execute(
                f"SELECT source, title FROM articles WHERE id IN ({','.join('?' * len(ids))})"
                " ORDER BY published_at DESC", ids).fetchall()
            outlets = {r["source"] for r in rows}
            print(f"\n  {len(rows)} notícias, {len(outlets)} jornais")
            for r in rows:
                print(f"    {names.get(r['source'], r['source']):<22} {r['title']}")
    if not found:
        print("Base de dados vazia — corra primeiro:  python -m app.ingest")


if __name__ == "__main__":
    main()
