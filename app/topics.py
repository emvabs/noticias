"""Topic extraction: what is unusually talked about right now, per scope.

Terms (unigrams, bigrams and capitalized proper-noun phrases) from the last 48
hours are scored by TF-IDF against the previous 7 days, so a term that is
common every week ranks low and a term that spiked today ranks high.

Run `python -m app.topics` to print the current topics per scope.
"""
import json
import re
from datetime import timedelta

from . import config
from .sentiment import normalize

WINDOW_HOURS = 48
BACKGROUND_DAYS = 7
MIN_ARTICLES = 3          # a topic must cover at least this many articles
MAX_TOPICS = 6          # the trending shortlist shown in the page
PROPER_NOUN_BOOST = 1.6   # favours "Banco de Portugal" over "banco"
MAX_PHRASE_WORDS = 4      # longer runs of capitals are menus, not topics
# Plain bigrams get no boost: an accidental word pair ("comboios afeta") would
# otherwise outrank the word that actually names the story ("greve").
KIND_ORDER = {"proper": 0, "unigram": 1, "bigram": 2}
OVERLAP_MERGE = 0.8       # merge terms sharing >80% of their articles

# Words are kept with their original case so proper nouns can be detected.
WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
# Lowercase connectors allowed inside a proper-noun phrase ("Casa da Música").
CONNECTORS = {"de", "da", "do", "das", "dos", "e", "of", "the", "and", "for", "in", "el", "la"}

STOPWORDS_PT = set("""
a à às ao aos aquela aquelas aquele aqueles aquilo as até com como da das de dela delas dele deles depois
do dos e é ela elas ele eles em entre era eram essa essas esse esses esta está estas este estes eu foi
fomos for foram fosse fui há isso isto já lhe lhes mais mas me mesmo meu meus minha minhas muito na não
nas nem no nos nós nossa nossas nosso nossos num numa o os ou para pela pelas pelo pelos por qual quando
que quem se sem ser seu seus só sua suas também te tem têm tenho ter teu teus tu tua tuas um uma umas uns
você vocês vos ainda apenas após cada contra desde diz disse dois duas mil onde pode podem pois qualquer
quase sobre sua sob são ter tinha vai vão vez ver ia ate mesma tres quatro cinco
""".split())

STOPWORDS_EN = set("""
a about after all also am an and any are as at be because been before being between both but by can could
did do does doing don down during each few for from further had has have having he her here hers him his
how i if in into is it its just me more most my no nor not now of off on once only or other our ours out
over own said same say says she should so some such than that the their them then there these they this
those through to too under until up very was we were what when where which while who whom why will with
would you your new one two three first last week year make made get got go goes going has been
""".split())


def _normalized(words):
    """Stopwords are compared against accent-stripped tokens, so strip them too."""
    return {normalize(w) for w in words}


def outlet_words():
    """Outlets talk about themselves ("CNN", "LPP"), which is not a topic."""
    words = set()
    for src in config.load_sources(enabled_only=False):
        for word in normalize(src["name"]).split():
            if len(word) > 2:
                words.add(word)
        words.add(normalize(src["id"]))      # "lpp", "oregioes"
    return words


def load_stoplist():
    path = config.path("config/stoplist.txt")
    words = set()
    if path.exists():
        for raw in open(path, encoding="utf-8"):
            word = normalize(raw.split("#", 1)[0].strip())
            if word:
                words.add(word)
    return words


def slugify(label):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalize(label))).strip("-")


def singular(term):
    """Crude singular form, for merging 'incêndio' with 'incêndios'."""
    parts = []
    for word in term.split():
        for suffix, replacement in (("oes", "ao"), ("aes", "ao"), ("ais", "al"),
                                    ("eis", "el"), ("ies", "y"), ("es", ""), ("s", "")):
            if len(word) > 4 and word.endswith(suffix):
                word = word[: -len(suffix)] + replacement
                break
        parts.append(word)
    return " ".join(parts)


def is_capitalized(word):
    first = word[0]
    return first.isupper() and not word.isupper() or (word.isupper() and len(word) > 2)


SENTENCE_RE = re.compile(r"[.!?:;\u2026]+\s+|\s+[\u2013\u2014-]\s+")


def candidate_terms(text, stopwords):
    """Return {key: (label, kind, position)} for unigrams, bigrams and proper phrases.

    `position` is the word index of the term's first occurrence, used to prefer
    terms that headlines lead with.
    """
    terms = {}
    offset = 0
    for sentence in SENTENCE_RE.split(text or ""):
        offset += _terms_in_sentence(sentence, stopwords, terms, offset)
    return terms


def _terms_in_sentence(text, stopwords, terms, offset=0):
    words = WORD_RE.findall(text or "")
    lowered = [normalize(w) for w in words]

    def add(key, label, kind, position):
        # Keep the first spelling seen; prefer a capitalized one if it shows up.
        if key not in terms:
            terms[key] = (label, kind, position)
        elif kind == "proper" and terms[key][1] != "proper":
            terms[key] = (label, kind, min(position, terms[key][2]))

    for i, word in enumerate(words):
        low = lowered[i]
        if len(low) > 2 and low not in stopwords:
            add(low, word, "unigram", offset + i)
        # bigram of two content words
        if i + 1 < len(words):
            nxt = lowered[i + 1]
            if len(low) > 2 and len(nxt) > 2 and low not in stopwords and nxt not in stopwords:
                add(f"{low} {nxt}", f"{word} {words[i + 1]}", "bigram", offset + i)

    # Capitalized phrases: Word (Connector? Word)+, skipping the sentence's first word.
    i = 1 if words else 0
    while i < len(words):
        if not is_capitalized(words[i]):
            i += 1
            continue
        phrase = [words[i]]
        j = i + 1
        while j < len(words):
            if is_capitalized(words[j]):
                phrase.append(words[j])
                j += 1
            elif (lowered[j] in CONNECTORS and j + 1 < len(words) and is_capitalized(words[j + 1])):
                phrase.extend([words[j], words[j + 1]])
                j += 2
            else:
                break
        if 1 < len(phrase) <= MAX_PHRASE_WORDS:
            label = " ".join(phrase)
            key = normalize(label)
            parts = key.split()
            content = [w for w in parts if w not in stopwords]
            # Drop a phrase made only of stopwords, and one that is nothing but
            # an outlet's name ("LPP Lisboa Para Pessoas" is a byline). A phrase
            # that merely contains one ("Banco de Portugal") is still a topic.
            if content and not all(w in OUTLETS for w in content):
                add(key, label, "proper", offset + i)
        i = max(j, i + 1)

    return len(words)


OUTLETS = outlet_words()


def article_text(row):
    return f"{row['title']}. {row['summary']}"


def extract(window_rows, background_rows, stopwords=None,
            max_topics=MAX_TOPICS, min_articles=MIN_ARTICLES):
    """Return a ranked list of topics: {slug, label, count, score, article_ids}."""
    if stopwords is None:
        stopwords = _normalized(STOPWORDS_PT | STOPWORDS_EN) | load_stoplist() | outlet_words()
    if not window_rows:
        return []

    postings = {}       # key -> set of article ids in the window
    labels = {}         # key -> display label
    kinds = {}          # key -> unigram / bigram / proper
    headline = {}       # key -> [times it appears in a title, sum of its position there]
    for row in window_rows:
        title_terms = candidate_terms(row["title"], stopwords)
        for key, (label, kind, position) in title_terms.items():
            seen = headline.setdefault(key, [0, 0])
            seen[0] += 1
            seen[1] += position
        for key, (label, kind, _) in {**candidate_terms(row["summary"], stopwords),
                                      **title_terms}.items():
            postings.setdefault(key, set()).add(row["id"])
            if key not in labels or (kind == "proper" and kinds.get(key) != "proper"):
                labels[key], kinds[key] = label, kind

    background = {}     # key -> number of older articles containing it
    for row in background_rows:
        for key in candidate_terms(article_text(row), stopwords):
            background[key] = background.get(key, 0) + 1

    n_window, n_background = len(window_rows), len(background_rows)
    scored = []
    for key, ids in postings.items():
        if len(ids) < min_articles:
            continue
        tf = len(ids) / n_window
        # Rare in the previous week -> high idf. +1 keeps it defined when unseen.
        idf = _log((n_background + 1) / (background.get(key, 0) + 1))
        boost = PROPER_NOUN_BOOST if kinds[key] == "proper" else 1.0
        titled, position_sum = headline.get(key, (0, 0))
        scored.append({"key": key, "slug": slugify(labels[key]), "label": labels[key],
                       "count": len(ids), "score": tf * idf * boost,
                       "article_ids": ids, "kind": kinds[key],
                       "in_titles": titled,
                       "title_position": position_sum / titled if titled else 99})

    # On a tie prefer a proper noun, then the term headlines lead with, then the
    # one appearing in more titles, then a single word over a word pair.
    scored.sort(key=lambda t: (-t["score"], KIND_ORDER[t["kind"]], -t["in_titles"],
                               t["title_position"], -t["count"], t["key"]))
    return _merge(scored)[:max_topics]


def _log(x):
    import math
    return math.log(x) + 1  # +1 so a term seen every day still scores above zero


def _merge(topics):
    """Drop near-duplicates: same singular form, or heavily overlapping article sets."""
    kept = []
    for topic in topics:                      # already sorted best first
        root = singular(topic["key"])
        duplicate = None
        for other in kept:
            if singular(other["key"]) == root:
                duplicate = other
                break
            overlap = len(topic["article_ids"] & other["article_ids"])
            if overlap and overlap / min(len(topic["article_ids"]), len(other["article_ids"])) > OVERLAP_MERGE:
                duplicate = other
                break
        if duplicate:
            # The winner keeps its own articles: unioning them would report a
            # count far larger than the number of articles actually about it.
            continue
        else:
            kept.append(topic)
    return kept


# ---------- database side ----------

def scopes(conn):
    """Every (scope, group) pair that has articles: topics are computed per pair."""
    return [(r[0], r[1]) for r in conn.execute(
        'SELECT DISTINCT scope, "group" FROM articles ORDER BY scope, "group"')]


def settings_for(group):
    """Window and thresholds, with the group's overrides from config.yaml.

    The independent outlets publish a few pieces a month, so 48 hours would
    almost never contain enough articles for a topic to reach the threshold.
    """
    get = lambda key, default: config.group_settings(group, "topics", key, default=default)  # noqa: E731
    return (get("window_hours", WINDOW_HOURS), get("background_days", BACKGROUND_DAYS),
            get("max_topics", MAX_TOPICS), get("min_articles", MIN_ARTICLES))


def compute_for_scope(conn, scope, group="mainstream", now=None):
    """Extract topics for one scope+group, store them and tag the articles."""
    from .ingest import iso, utcnow
    now = now or utcnow()
    window_hours, background_days, max_topics, min_articles = settings_for(group)
    window_start = iso(now - timedelta(hours=window_hours))
    background_start = iso(now - timedelta(days=background_days + window_hours / 24))

    window = conn.execute(
        'SELECT id, title, summary FROM articles WHERE scope = ? AND "group" = ? AND published_at >= ?',
        (scope, group, window_start)).fetchall()
    # Anything in the scope that is not in this window is the baseline: the
    # group's own older pieces plus the other groups' articles. Without this a
    # slow-publishing group has no history, TF-IDF goes flat and common words
    # like "aparece" or "durante" come out on top.
    background = conn.execute(
        """SELECT id, title, summary FROM articles
           WHERE scope = ? AND published_at >= ?
             AND ("group" != ? OR published_at < ?)""",
        (scope, background_start, group, window_start)).fetchall()

    topics = extract(window, background, max_topics=max_topics, min_articles=min_articles)

    by_article = {}
    for topic in topics:
        for article_id in topic["article_ids"]:
            by_article.setdefault(article_id, []).append(topic["slug"])

    computed_at = iso(now)
    conn.execute('DELETE FROM topics_cache WHERE scope = ? AND "group" = ?', (scope, group))
    conn.executemany(
        """INSERT INTO topics_cache (scope, "group", slug, label, count, rank, computed_at)
           VALUES (?,?,?,?,?,?,?)""",
        [(scope, group, t["slug"], t["label"], t["count"], i, computed_at)
         for i, t in enumerate(topics)])
    conn.execute('UPDATE articles SET topics = \'[]\' WHERE scope = ? AND "group" = ?',
                 (scope, group))
    conn.executemany("UPDATE articles SET topics = ? WHERE id = ?",
                     [(json.dumps(slugs), article_id) for article_id, slugs in by_article.items()])
    conn.commit()
    return topics


def recompute_all(conn, now=None):
    return {(scope, group): compute_for_scope(conn, scope, group, now)
            for scope, group in scopes(conn)}


def current(conn, scope, group="mainstream"):
    rows = conn.execute(
        """SELECT slug, label, count, computed_at FROM topics_cache
           WHERE scope = ? AND "group" = ? ORDER BY rank""", (scope, group)).fetchall()
    return [dict(r) for r in rows]


def main():
    """CLI: print the current topics per scope and group."""
    from . import db
    conn = db.connect()
    pairs = scopes(conn)
    for scope, group in pairs:
        found = current(conn, scope, group)
        hours, _, max_topics, min_articles = settings_for(group)
        print(f"\n=== {scope} / {group} ({len(found)} tópicos, janela {hours}h, "
              f"mín. {min_articles} artigos"
              + (f", calculados {found[0]['computed_at']}" if found else "") + ") ===")
        for i, t in enumerate(found, 1):
            print(f"{i:2}. {t['label']:<34} {t['count']:>4} artigos   {t['slug']}")
    if not pairs:
        print("Base de dados vazia — corra primeiro:  python -m app.ingest")


if __name__ == "__main__":
    main()
