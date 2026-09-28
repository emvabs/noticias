"""Who is behind each article: bylines, outlet and journalist profiles.

Bylines are stored as cleaned names (``articles.authors``) and resolved here at
read time, so editing ``profiles/aliases.txt`` applies to stored articles too.
Profiles are hand-researched YAML files in ``profiles/outlets`` and
``profiles/journalists``; nothing is fetched at run time.
"""
import html
import json
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta

import yaml

from . import config

PROFILES = config.ROOT / "profiles"
SLUG_RE = re.compile(r"^[a-z0-9-]+$")

# --------------------------------------------------------------------------
# Cleaning bylines
# --------------------------------------------------------------------------
SPLIT_RE = re.compile(r"\s*(?:,|;|\s&\s|\sand\s|\swith\s)\s*")
# Portuguese "e" also joins surnames ("Pedro Adão e Silva"): split on it only
# when both sides look like full names.
E_RE = re.compile(r"\s+e\s+")
# "Robert Tait in Washington", "Ed Walton for MetDesk", "Nick Visser (earlier)"
TAIL_RES = [
    re.compile(r"\s*\([^)]*\)\s*$"),
    re.compile(r"\s+(?:in|for)\s+[A-Z][\w'’. -]*$"),
]
# Guardian appends the job title: "Amy Hawkins Senior China correspondent".
# The title's first words are capitalised like a name, so keep the first two words.
ROLE_RE = re.compile(r"\b(?:correspondent|editor|reporter|writer|columnist|critic|analyst)s?$", re.I)
NOT_A_NAME = {"agencies", "agency", "staff", ""}


def _clean_one(name):
    name = html.unescape(name).strip(" . ")
    for pattern in TAIL_RES:
        name = pattern.sub("", name).strip()
    if ROLE_RE.search(name):
        name = " ".join(name.split()[:2])
    return " ".join(name.split())


def split_authors(raw):
    """One byline string -> a list of names.

    "Rachel Savage in Johannesburg and agencies" -> ["Rachel Savage"]
    "Mark Brown, Lisa O’Carroll and Shane Harrison" -> three names
    """
    raw = html.unescape(raw or "").strip()
    # e-mail addresses (Público's feed) and production credits are not bylines
    if not raw or "@" in raw or raw.lower().startswith(("presented by", "http")):
        return []
    if ROLE_RE.search(raw):   # a job title can itself contain "and"
        raw = _clean_one(raw)
    parts = []
    for part in SPLIT_RE.split(raw):
        pieces = E_RE.split(part)
        parts += pieces if all(len(p.split()) >= 2 for p in pieces) else [part]
    names = []
    for part in parts:
        name = _clean_one(part)
        if name.lower() not in NOT_A_NAME and name not in names:
            names.append(name)
    return names


def entry_authors(entry):
    """Names from a feedparser entry (``authors`` list, falling back to ``author``)."""
    raws = [a.get("name") or "" for a in entry.get("authors") or [] if isinstance(a, dict)]
    if not any(raws):
        raws = [entry.get("author") or ""]
    names = []
    for raw in raws:
        for name in split_authors(raw):
            if name not in names:
                names.append(name)
    return names


LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.I | re.S)
META_RE = re.compile(
    r'<meta[^>]+(?:name|property)="(?:author|article:author|parsely-author)"[^>]*'
    r'content="([^"]+)"', re.I)


def _ld_authors(node, found):
    if isinstance(node, list):
        for item in node:
            _ld_authors(item, found)
    elif isinstance(node, dict):
        if "author" in node:
            authors = node["author"]
            for a in authors if isinstance(authors, list) else [authors]:
                if isinstance(a, dict) and a.get("@type") == "Person" and a.get("name"):
                    found.append(a["name"])
                elif isinstance(a, str):
                    found.append(a)
        for key in ("@graph", "mainEntity"):
            if key in node:
                _ld_authors(node[key], found)


def page_authors(page):
    """Bylines from an article page: JSON-LD first, then <meta name="author">."""
    found = []
    for block in LD_RE.findall(page or ""):
        try:
            _ld_authors(json.loads(block.strip()), found)
        except ValueError:
            continue
    if not found:
        found = [m for m in META_RE.findall(page or "") if "://" not in m]
    names = []
    for raw in found:
        for name in split_authors(raw):
            if name not in names:
                names.append(name)
    return names


# --------------------------------------------------------------------------
# Resolving names to profiles
# --------------------------------------------------------------------------
def slugify(name):
    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def load_aliases():
    """profiles/aliases.txt: ``Byline = journalist:slug | outlet:id | -``."""
    aliases = {}
    path = PROFILES / "aliases.txt"
    if not path.exists():
        return aliases
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        name, target = (s.strip() for s in line.split("=", 1))
        aliases[slugify(name)] = target
    return aliases


# A byline carrying one of these words is a desk, a team or an agency, not a person.
NON_PERSON_WORDS = {"redacao", "redaccao", "staff", "agencia", "agencias", "agency",
                    "agencies", "newsroom", "desk", "equipa", "team", "varios", "autores",
                    "correspondentes", "reportagem", "editorial"}


def _tokens(slug):
    return tuple(t for t in slug.split("-") if t)


def _contains(tokens, part):
    n = len(part)
    return any(tokens[i:i + n] == part for i in range(len(tokens) - n + 1))


def _outlet_names():
    """Every outlet and agency name (sources.yaml + profiles/outlets) -> id."""
    names = {slugify(s["name"]): s["id"] for s in config.load_sources(enabled_only=False)}
    names.update({slugify(s["id"]): s["id"] for s in config.load_sources(enabled_only=False)})
    for path in sorted((PROFILES / "outlets").glob("*.yaml")):
        names.setdefault(slugify(path.stem), path.stem)
        data = _read(path)
        names.setdefault(slugify(data.get("name", "")), path.stem)
    return names


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


class Resolver:
    """Maps bylines to {name, kind, id, has_profile}. Build one per request."""

    def __init__(self):
        self.aliases = load_aliases()
        self.outlets = _outlet_names()
        # Longest names first, so "Redação CNN Portugal" is CNN Portugal, not an outlet
        # whose name is a single word of it; agencies win ties ("Lusa/Reuters").
        sources = source_ids()
        self.outlet_tokens = sorted(((_tokens(k), v) for k, v in self.outlets.items() if k),
                                    key=lambda tv: (-len(tv[0]), tv[1] in sources))
        self.journalist_files = {p.stem for p in (PROFILES / "journalists").glob("*.yaml")}
        self.outlet_files = {p.stem for p in (PROFILES / "outlets").glob("*.yaml")}

    def resolve(self, name):
        name = " ".join(name.split())
        key = slugify(name)
        target = self.aliases.get(key)
        if target == "-":
            return {"name": name, "kind": "other", "id": None, "has_profile": False}
        if target and ":" in target:
            kind, ident = (s.strip() for s in target.split(":", 1))
        elif key in self.outlets:
            kind, ident = "outlet", self.outlets[key]
        elif outlet := self.outlet_in(key):
            kind, ident = "outlet", outlet
        elif set(_tokens(key)) & NON_PERSON_WORDS:
            return {"name": name, "kind": "other", "id": None, "has_profile": False}
        else:
            kind, ident = "journalist", key
        files = self.outlet_files if kind == "outlet" else self.journalist_files
        return {"name": name, "kind": kind, "id": ident, "has_profile": ident in files}

    def outlet_in(self, key):
        """The outlet named inside a byline: "Redação CNN Portugal", "Al Jazeera Staff"."""
        tokens = _tokens(key)
        for part, ident in self.outlet_tokens:
            if _contains(tokens, part):
                return ident
        return None

    def resolve_all(self, stored):
        names = json.loads(stored) if stored else []
        out, seen = [], set()
        for name in names:
            r = self.resolve(name)
            if (r["kind"], r["id"] or r["name"]) not in seen:
                seen.add((r["kind"], r["id"] or r["name"]))
                out.append(r)
        return out


def outlet(ident):
    return _read(PROFILES / "outlets" / f"{ident}.yaml") if SLUG_RE.match(ident) else {}


def journalist(slug):
    return _read(PROFILES / "journalists" / f"{slug}.yaml") if SLUG_RE.match(slug) else {}


# --------------------------------------------------------------------------
# Local statistics (what this app has seen, independent of any research)
# --------------------------------------------------------------------------
def _stats(rows, names):
    labels = Counter(r["label"] for r in rows)
    total = len(rows)
    return {
        "articles": total,
        "outlets": [names.get(s, s) for s, _ in Counter(r["source"] for r in rows).most_common()],
        "labels": {k: labels.get(k, 0) for k in ("positive", "neutral", "negative")},
        "first": min((r["published_at"] for r in rows), default=None),
        "last": max((r["published_at"] for r in rows), default=None),
    }


def byline_articles(conn, kind, ident, resolver=None):
    """Stored articles signed by one journalist (or agency) - resolved, so aliases count."""
    resolver = resolver or Resolver()
    rows = conn.execute("""SELECT source, label, published_at, authors FROM articles
                           WHERE authors IS NOT NULL AND authors != '[]'""").fetchall()
    return [r for r in rows
            if any(a["kind"] == kind and a["id"] == ident for a in resolver.resolve_all(r["authors"]))]


def source_names():
    return {s["id"]: s["name"] for s in config.load_sources(enabled_only=False)}


def source_ids():
    return set(source_names())


def journalist_stats(conn, slug, resolver=None):
    return _stats(byline_articles(conn, "journalist", slug, resolver), source_names())


def outlet_stats(conn, ident):
    """Articles published by an outlet - or, for an agency, articles carrying its byline."""
    if ident in source_names():
        rows = conn.execute("SELECT source, label, published_at FROM articles WHERE source = ?",
                            (ident,)).fetchall()
    else:
        rows = byline_articles(conn, "outlet", ident)
    return _stats(rows, source_names())


def settings():
    cfg = config.load_config().get("profiles", {})
    return {"stale_after_days": cfg.get("stale_after_days", 180),
            "pending_min_articles": cfg.get("pending_min_articles", 3)}


def is_stale(profile, today=None):
    """A profile not checked for ``profiles.stale_after_days`` (or never) needs a review."""
    checked = profile.get("last_checked")
    if isinstance(checked, str):
        try:
            checked = date.fromisoformat(checked)
        except ValueError:
            checked = None
    if not isinstance(checked, date):
        return True
    return (today or date.today()) - checked > timedelta(days=settings()["stale_after_days"])


def stale_profiles(today=None):
    """Every outlet and journalist profile due for a review, oldest first."""
    out = []
    for kind, folder in (("outlet", "outlets"), ("journalist", "journalists")):
        for path in sorted((PROFILES / folder).glob("*.yaml")):
            data = _read(path)
            if is_stale(data, today):
                out.append({"kind": kind, "id": path.stem, "name": data.get("name", path.stem),
                            "last_checked": str(data.get("last_checked") or "")})
    return sorted(out, key=lambda p: p["last_checked"])


def suspect(name):
    """Why a byline may not be a person (to add to aliases.txt), or None."""
    words = name.split()
    if len(words) == 1:
        return "uma só palavra"
    if name.isupper():
        return "tudo em maiúsculas"
    return None


def pending(conn):
    """Journalists seen in stored articles without a profile file, most articles first."""
    resolver = Resolver()
    outlet_names = source_names()
    counts, names, outlets = Counter(), {}, {}
    for r in conn.execute("""SELECT source, authors FROM articles
                             WHERE authors IS NOT NULL AND authors != '[]'"""):
        for a in resolver.resolve_all(r["authors"]):
            if a["kind"] == "journalist" and not a["has_profile"]:
                counts[a["id"]] += 1
                names.setdefault(a["id"], a["name"])
                outlets.setdefault(a["id"], set()).add(r["source"])
    return [{"slug": s, "name": names[s], "articles": n,
             "outlets": sorted(outlet_names.get(o, o) for o in outlets[s]),
             "suspect": suspect(names[s])}
            for s, n in counts.most_common()]


def main(argv=None):
    """What still needs research: python -m app.profiles [--min N]."""
    import argparse
    import sqlite3

    parser = argparse.ArgumentParser(prog="python -m app.profiles",
                                     description="Jornalistas sem perfil e perfis a rever.")
    parser.add_argument("--min", type=int, default=settings()["pending_min_articles"],
                        help="mostrar só quem tem pelo menos N notícias (omissão: %(default)s)")
    args = parser.parse_args(argv)

    db_path = config.path(config.load_config()["storage"]["database"])
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)   # read-only: safe while the server runs
    conn.row_factory = sqlite3.Row
    todo = [p for p in pending(conn) if p["articles"] >= args.min]
    print(f"Jornalistas sem perfil com {args.min}+ notícias: {len(todo)}")
    for p in todo:
        flag = f"  <- {p['suspect']}? talvez seja para aliases.txt" if p["suspect"] else ""
        print(f"{p['articles']:4}  {p['slug']:32} {p['name']}  ({', '.join(p['outlets'])}){flag}")
    stale = stale_profiles()
    print(f"\nPerfis por rever há mais de {settings()['stale_after_days']} dias: {len(stale)}")
    for p in stale:
        folder = "outlets" if p["kind"] == "outlet" else "journalists"
        print(f"  {p['last_checked'] or 'sem data':10}  profiles/{folder}/{p['id']}.yaml  {p['name']}")


if __name__ == "__main__":
    main()
