"""Search box: whole-word matching plus an editable synonym list.

Articles store `search_text`: the headline and summary, lowercased, without
accents, with punctuation turned into spaces and a space at each end. That
padding is what makes whole-word matching possible with plain SQL LIKE:

    " ia "      matches " ... sobre ia hoje "      (the word)
                but not " ... mais praia ainda "   (letters inside a word)

Short words must match exactly; longer ones may match a suffix, so "incendio"
still finds "incêndios". Synonyms come from config/synonyms.txt, so searching
"IA" also finds "Inteligência Artificial".

Acronyms (IA, UE, EUA...) are matched case-sensitively against `raw_text`,
which keeps the original capitals and accents. Without that, stripping accents
would make "IA" match the very common Portuguese word "aí".
"""
import re

from . import config
from .sentiment import normalize

# Words this short must match exactly ("ia" must not match "ianque"); longer
# words match a prefix, which covers plurals and simple inflections.
EXACT_MAX_LEN = 3
NON_WORD_RE = re.compile(r"[^a-z0-9]+")
RAW_NON_WORD_RE = re.compile(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+")
ACRONYM_MAX_LEN = 5      # "IA", "UE", "EUA", "OTAN"; longer is a word, not an acronym


def is_acronym(term):
    """A short all-capitals term, as written by the user or in synonyms.txt."""
    return term.isupper() and term.isalpha() and len(term) <= ACRONYM_MAX_LEN


def raw_searchable(text):
    """Like searchable(), but keeping capitals and accents for acronym matching."""
    cleaned = RAW_NON_WORD_RE.sub(" ", text or "").strip()
    return f" {cleaned} " if cleaned else " "


def searchable(text):
    """Normalize text for storage/matching: ' palavra outra palavra '."""
    cleaned = NON_WORD_RE.sub(" ", normalize(text or "")).strip()
    return f" {cleaned} " if cleaned else " "


def words(query):
    """The query's words, normalized; punctuation is dropped."""
    return searchable(query).split()


_cache = {"mtime": None, "groups": []}


def load_synonyms():
    """Return a list of groups; each group is a list of normalized phrases."""
    path = config.path("config/synonyms.txt")
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    if _cache["mtime"] == mtime:
        return _cache["groups"]

    groups = []
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        terms = []
        for part in line.split(","):
            written = part.strip()
            phrase = " ".join(words(written))
            if phrase and phrase not in [t["phrase"] for t in terms]:
                terms.append({"phrase": phrase, "written": written,
                              "acronym": is_acronym(written)})
        if len(terms) > 1:
            groups.append(terms)
    _cache.update(mtime=mtime, groups=groups)
    return groups


def variants(query):
    """Terms to look for: what was typed, plus any synonyms of it.

    Only a query that *is* one of a group's terms expands; typing several words
    that merely contain a term is taken at face value.
    """
    phrase = " ".join(words(query))
    if not phrase:
        return []
    typed = (query or "").strip()
    found = [{"phrase": phrase, "written": typed, "acronym": is_acronym(typed)}]
    for group in load_synonyms():
        if any(term["phrase"] == phrase for term in group):
            # The typed form decides how the query itself is matched; the
            # synonyms carry the spelling from the file.
            found[0]["acronym"] = found[0]["acronym"] or next(
                t["acronym"] for t in group if t["phrase"] == phrase)
            found += [t for t in group if t["phrase"] != phrase]
    return found


def expand(query):
    """The synonym phrases a query is also searched under (for the page)."""
    return [t["written"] for t in variants(query)[1:]]


def _like_for(phrase):
    """LIKE pattern matching a phrase on word boundaries."""
    parts = phrase.split()
    pattern = " " + " ".join(parts)
    # A short final word must end at a word boundary; a longer one may continue
    # ("incendio" matches "incendios"), which keeps plurals working.
    if len(parts[-1]) <= EXACT_MAX_LEN:
        pattern += " "
    return f"%{pattern}%"


def sql_clause(query, column="search_text", raw_column="raw_text"):
    """Return (sql, params) selecting articles that match the query.

    Words of a term must all be present, and any synonym may match instead.
    Acronyms are matched with GLOB, which is case-sensitive in SQLite, so "IA"
    does not match "ia" inside ordinary prose.
    """
    terms = variants(query)
    if not terms:
        return "", []

    clauses, params = [], []
    for term in terms:
        conditions = []
        if term["acronym"]:
            conditions.append(f"{raw_column} GLOB ?")
            params.append(f"* {term['written'].upper()} *")
        else:
            for word in term["phrase"].split():
                conditions.append(f"{column} LIKE ?")
                params.append(_like_for(word))
        clauses.append("(" + " AND ".join(conditions) + ")")
    return "(" + " OR ".join(clauses) + ")", params
