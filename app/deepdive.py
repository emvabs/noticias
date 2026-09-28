"""Deep Dive: findings about one outlet or journalist, and the silences table.

Two kinds of finding, never mixed up:

* researched  - claims from the profile files, each with its source URL;
* data        - tendencies measured in the article archive, each with the
                articles that support it and the numbers behind it.

The wording is "tendência nos dados", never "viés": the sentiment labels are
word lists, and a topic being frequent says what an outlet covers, not what
it thinks. A finding with no supporting article or source is not produced.
"""
import json
import math
from collections import Counter
from datetime import timedelta

from . import archive, config, ownership, profiles
from . import topics as topics_module
from .ingest import iso, utcnow

AGENCIES = ownership.AGENCIES
LABELS = {"positive": "positivas", "neutral": "neutras", "negative": "negativas"}
# How the baseline reads in a sentence: (after "que", after "contra X%")
OUTLET_BASELINE = {"portugal": ("os outros jornais portugueses", "nos outros jornais portugueses"),
                   "world": ("os outros meios internacionais", "nos outros meios internacionais")}


def settings():
    cfg = config.load_config().get("deepdive", {})
    return {
        "window_days": cfg.get("window_days", 90),
        "min_articles": cfg.get("min_articles", 15),
        "tone_diff_pp": cfg.get("tone_diff_pp", 10),
        "topic_lift": cfg.get("topic_lift", 2.0),
        "topic_min_articles": cfg.get("topic_min_articles", 5),
        "max_topics": cfg.get("max_topics", 3),
        "articles_per_finding": cfg.get("articles_per_finding", 50),
        "silence_min_outlet_articles": cfg.get("silence_min_outlet_articles", 30),
        "silence_min_coverage": cfg.get("silence_min_coverage", 0.6),
        "silence_max_ratio": cfg.get("silence_max_ratio", 0.25),
        "silence_min_expected": cfg.get("silence_min_expected", 3),
    }


# --------------------------------------------------------------------------
# Loading the archive
# --------------------------------------------------------------------------
def load(conn, window_days=None, now=None):
    """Archived articles of the window, with bylines resolved once."""
    window_days = window_days or settings()["window_days"]
    since = iso((now or utcnow()) - timedelta(days=window_days))
    rows = conn.execute(
        """SELECT url, source, scope, "group", published_at, title, authors, label,
                  topics, owner_mentions
           FROM article_archive WHERE published_at >= ? ORDER BY published_at DESC""",
        (since,)).fetchall()
    resolver = profiles.Resolver()
    names = _source_names()
    cache = {}
    out = []
    for r in rows:
        a = dict(r)
        a["source_name"] = names.get(a["source"], a["source"])
        key = a["authors"]
        if key not in cache:
            cache[key] = resolver.resolve_all(key)
        a["bylines"] = cache[key]
        a["topics"] = json.loads(a["topics"] or "[]")
        a["owner_mentions"] = json.loads(a["owner_mentions"] or "[]")
        out.append(a)
    return out


def _signed_by(a, kind, ident):
    return any(b["kind"] == kind and b["id"] == ident for b in a["bylines"])


def entity_rows(rows, kind, ident):
    if kind == "journalist":
        return [a for a in rows if _signed_by(a, "journalist", ident)]
    if ident in _source_ids():
        return [a for a in rows if a["source"] == ident]
    return [a for a in rows if _signed_by(a, "outlet", ident)]        # an agency


def _source_ids():
    return {s["id"] for s in config.load_sources(enabled_only=False)}


def _source_names():
    return {s["id"]: s["name"] for s in config.load_sources(enabled_only=False)}


def _brief(a):
    """What the page needs to draw an article node."""
    return {"url": a["url"], "title": a["title"], "source": a["source"],
            "source_name": a["source_name"], "label": a["label"], "published_at": a["published_at"]}


def _evidence(rows, limit):
    return {"total": len(rows), "articles": [_brief(a) for a in rows[:limit]]}


def _num(x):
    """37.05 -> "37,05", 5.0 -> "5": decimals the Portuguese way."""
    return f"{x:g}".replace(".", ",")


def _pct(n, total):
    return round(100 * n / total) if total else 0


# --------------------------------------------------------------------------
# Index: who can be chosen
# --------------------------------------------------------------------------
def index(conn, kind):
    rows = load(conn)
    names = _source_names()
    outlet_files = {p.stem for p in (profiles.PROFILES / "outlets").glob("*.yaml")}
    if kind == "outlet":
        counts = Counter(a["source"] for a in rows)
        for a in rows:
            for b in a["bylines"]:
                if b["kind"] == "outlet" and b["id"] in AGENCIES and b["id"] != a["source"]:
                    counts[b["id"]] += 1
        enabled = {s["id"] for s in config.load_sources()}
        ids = (enabled | AGENCIES) & (set(counts) | outlet_files | enabled)
        out = [{"id": i, "name": names.get(i) or profiles.outlet(i).get("name", i),
                "articles": counts.get(i, 0), "has_profile": i in outlet_files,
                "agency": i in AGENCIES} for i in ids]
    else:
        counts, first_name, outlets = Counter(), {}, {}
        for a in rows:
            for b in a["bylines"]:
                if b["kind"] == "journalist":
                    counts[b["id"]] += 1
                    first_name.setdefault(b["id"], (b["name"], b["has_profile"]))
                    outlets.setdefault(b["id"], Counter())[a["source"]] += 1
        out = [{"id": i, "name": first_name[i][0], "articles": n, "has_profile": first_name[i][1],
                "outlets": [names.get(o, o) for o, _ in outlets[i].most_common()]}
               for i, n in counts.items()]
    return sorted(out, key=lambda e: (-e["articles"], e["name"].lower()))


# --------------------------------------------------------------------------
# Data findings
# --------------------------------------------------------------------------
def tone_finding(mine, base, base_name, cfg):
    """base_name = (after "que", after "contra X%")."""
    n, nb = len(mine), len(base)
    if n < cfg["min_articles"] or nb < cfg["min_articles"]:
        return None
    share = {k: _pct(sum(a["label"] == k for a in mine), n) for k in LABELS}
    base_share = {k: _pct(sum(a["label"] == k for a in base), nb) for k in LABELS}
    diffs = {k: share[k] - base_share[k] for k in ("negative", "positive")}
    label = max(diffs, key=lambda k: abs(diffs[k]))
    if abs(diffs[label]) < cfg["tone_diff_pp"]:
        return None
    more = diffs[label] > 0
    word = LABELS[label]
    than, in_ = base_name
    title = (f"Mais notícias {word} que {than}" if more
             else f"Menos notícias {word} que {than}")
    text = (f"{share[label]}% das {n} notícias são {word}, contra {base_share[label]}% "
            f"{in_} ({nb} notícias): {abs(diffs[label])} pontos de diferença.")
    supporting = [a for a in mine if (a["label"] == label) == more]
    return {"kind": "tone", "icon": "📊", "title": title, "text": text,
            "numbers": {"share": share, "baseline": base_share, "n": n, "n_baseline": nb},
            **_evidence(supporting, cfg["articles_per_finding"])}


def _terms(rows, stopwords):
    postings, labels, kinds = {}, {}, {}
    for i, a in enumerate(rows):
        for key, (label, kind, _) in topics_module.candidate_terms(a["title"], stopwords).items():
            postings.setdefault(key, set()).add(i)
            if key not in labels or (kind == "proper" and kinds[key] != "proper"):
                labels[key], kinds[key] = label, kind
    return postings, labels, kinds


def topic_findings(mine, base, base_name, cfg):
    """Terms in the headlines far more frequent here than in the baseline."""
    n, nb = len(mine), len(base)
    if n < cfg["min_articles"] or nb < cfg["min_articles"]:
        return []
    stopwords = (topics_module._normalized(topics_module.STOPWORDS_PT | topics_module.STOPWORDS_EN)
                 | topics_module.load_stoplist() | topics_module.outlet_words())
    postings, labels, kinds = _terms(mine, stopwords)
    base_postings, _, _ = _terms(base, stopwords)
    scored = []
    for key, ids in postings.items():
        count = len(ids)
        if count < cfg["topic_min_articles"]:
            continue
        in_base = len(base_postings.get(key, ()))
        # +1 on the baseline: a term it never uses is "much more", not infinitely more
        lift = (count / n) / ((in_base + 1) / (nb + 1))
        if lift < cfg["topic_lift"]:
            continue
        boost = 1.3 if kinds[key] == "proper" else 1.0
        scored.append({"key": key, "label": labels[key], "count": count, "in_base": in_base,
                       "lift": lift, "score": count * math.log(lift) * boost, "article_ids": ids})
    scored.sort(key=lambda t: -t["score"])
    kept = topics_module._merge(scored)[:cfg["max_topics"]]
    out = []
    for t in kept:
        supporting = [mine[i] for i in sorted(t["article_ids"])]
        out.append({
            "kind": "topic", "icon": "📊",
            "title": f"Destaca «{t['label']}»",
            "text": (f"Aparece em {t['count']} de {n} títulos ({_pct(t['count'], n)}%), "
                     f"{_num(round(t['lift'], 1))}× mais do que {base_name[1]} "
                     f"({t['in_base']} de {nb})."),
            "numbers": {"count": t["count"], "n": n, "in_baseline": t["in_base"],
                        "n_baseline": nb, "lift": round(t["lift"], 2)},
            **_evidence(supporting, cfg["articles_per_finding"])})
    return out


def agency_finding(mine, base, cfg):
    """How much of an outlet is agency copy (only articles whose byline is known)."""
    known = [a for a in mine if a["authors"] not in (None, "[]")]
    known_base = [a for a in base if a["authors"] not in (None, "[]")]
    if len(known) < cfg["min_articles"]:
        return None

    def from_agency(a):
        return any(b["kind"] == "outlet" and b["id"] in AGENCIES for b in a["bylines"])

    agency = [a for a in known if from_agency(a)]
    if not agency:
        return None
    share = _pct(len(agency), len(known))
    base_share = _pct(sum(from_agency(a) for a in known_base), len(known_base))
    names = Counter(b["name"] for a in agency for b in a["bylines"]
                    if b["kind"] == "outlet" and b["id"] in AGENCIES)
    return {"kind": "agency", "icon": "📊",
            "title": f"{share}% das notícias assinadas são de agência",
            "text": (f"{len(agency)} de {len(known)} notícias com autor conhecido vêm de agências "
                     f"({', '.join(n for n, _ in names.most_common(3))}); "
                     f"a média dos outros jornais é {base_share}%."),
            "numbers": {"share": share, "baseline": base_share, "n": len(known)},
            **_evidence(agency, cfg["articles_per_finding"])}


def owner_finding(mine, owners, cfg):
    flagged = [a for a in mine if a["owner_mentions"]]
    if not flagged:
        return None
    named = Counter(k for a in flagged for k in a["owner_mentions"])
    return {"kind": "owner", "icon": "⚠️",
            "title": f"Menciona o próprio dono ou grupo em {len(flagged)} notícias",
            "text": (f"Nomes encontrados: {', '.join(f'{k} ({n})' for k, n in named.most_common(5))}. "
                     + (f"Donos: {'; '.join(owners)}." if owners else "")),
            "numbers": {"count": len(flagged), "n": len(mine)},
            **_evidence(flagged, cfg["articles_per_finding"])}


# --------------------------------------------------------------------------
# Researched findings (from the profile files)
# --------------------------------------------------------------------------
def _sources(items):
    urls = []
    for item in items:
        src = item.get("source") if isinstance(item, dict) else None
        for url in src if isinstance(src, list) else [src]:
            if url and url not in urls:
                urls.append(url)
    return urls


def researched_findings(profile, kind):
    out = []
    if kind == "outlet" and profile.get("owners"):
        owners = profile["owners"]
        text = "; ".join(f"{o['name']}" + (f" ({_num(o['share'])}%)" if o.get("share") else "")
                         + (f" via {o['via']}" if o.get("via") else "") for o in owners)
        out.append({"kind": "researched", "icon": "📋", "title": "Propriedade", "text": text,
                    "sources": _sources(owners)})
    if kind == "outlet" and profile.get("funding"):
        out.append({"kind": "researched", "icon": "📋", "title": "Financiamento",
                    "text": " ".join(i["text"] for i in profile["funding"][:2]),
                    "sources": _sources(profile["funding"])})
    if kind == "journalist" and profile.get("career"):
        out.append({"kind": "researched", "icon": "📋", "title": "Percurso",
                    "text": " ".join(i["text"] for i in profile["career"]),
                    "sources": _sources(profile["career"])})
    links = profile.get("political_links") or {}
    items = [("Oficial: ", i) for i in links.get("official") or []] + \
            [("Reportado: ", i) for i in links.get("reported") or []]
    if items:
        out.append({"kind": "researched", "icon": "📋", "title": "Ligações políticas",
                    "text": " ".join(prefix + i["text"] for prefix, i in items),
                    "sources": _sources([i for _, i in items])})
    if profile.get("narrative"):
        out.append({"kind": "researched", "icon": "📋", "title": "Narrativa e críticas documentadas",
                    "text": " ".join(i["text"] for i in profile["narrative"]),
                    "sources": _sources(profile["narrative"])})
    return [f for f in out if f["sources"]]      # no source, no finding


# --------------------------------------------------------------------------
# One entity
# --------------------------------------------------------------------------
def _scope_of(rows):
    return Counter(a["scope"] for a in rows).most_common(1)[0][0] if rows else "portugal"


def dive(conn, kind, ident):
    cfg = settings()
    rows = load(conn, cfg["window_days"])
    names = _source_names()
    mine = entity_rows(rows, kind, ident)
    profile = profiles.outlet(ident) if kind == "outlet" else profiles.journalist(ident)

    if kind == "outlet":
        name = names.get(ident) or profile.get("name") or ident
        scope = _scope_of(mine)
        if ident in _source_ids():
            base = [a for a in rows if a["scope"] == scope and a["source"] != ident]
        else:   # an agency: everything else in the scope not carrying its byline
            base = [a for a in rows if a["scope"] == scope and not _signed_by(a, "outlet", ident)]
        base_name = OUTLET_BASELINE.get(scope, OUTLET_BASELINE["portugal"])
        outlets = [name]
    else:
        name = profile.get("name") or next(
            (b["name"] for a in mine for b in a["bylines"] if b["id"] == ident), ident)
        sources = {a["source"] for a in mine}
        base = [a for a in rows if a["source"] in sources and not _signed_by(a, "journalist", ident)]
        outlets = [names.get(s, s) for s, _ in Counter(a["source"] for a in mine).most_common()]
        # "as restantes notícias de X" avoids "do Expresso" vs "da CNN Portugal"
        where = outlets[0] if len(outlets) == 1 else "desses jornais"
        where = where if where.startswith("desses") else f"de {where}"
        base_name = (f"as restantes notícias {where}", f"nas restantes notícias {where}")

    findings = researched_findings(profile, kind)
    data = []
    for f in (tone_finding(mine, base, base_name, cfg),
              *topic_findings(mine, base, base_name, cfg),
              agency_finding(mine, base, cfg) if kind == "outlet" and ident not in AGENCIES else None,
              owner_finding(mine, [o["name"] for o in profile.get("owners") or []], cfg)
              if kind == "outlet" else None):
        if f:
            data.append(f)
    if kind == "outlet" and ident in _source_ids():
        data += silence_findings(conn, rows, ident, cfg)
    findings += data
    for i, f in enumerate(findings):
        f["id"] = f"f{i}"

    counts = Counter(a["label"] for a in mine)
    return {
        "kind": kind, "id": ident, "name": name, "outlets": outlets,
        "has_profile": bool(profile), "stale": bool(profile) and profiles.is_stale(profile),
        "role": profile.get("role") or profile.get("type"),
        "window_days": cfg["window_days"], "min_articles": cfg["min_articles"],
        "articles": len(mine),
        "labels": {k: counts.get(k, 0) for k in LABELS},
        "enough_data": len(mine) >= cfg["min_articles"],
        "summary": summary(name, kind, profile, mine, data, cfg),
        "findings": findings,
    }


def summary(name, kind, profile, mine, data, cfg):
    """Two or three plain sentences built from the findings, with their numbers."""
    parts = []
    if kind == "outlet" and profile.get("owners"):
        owners = [o["name"] + (f" ({_num(o['share'])}%)" if o.get("share") and o["share"] < 100 else "")
                  for o in profile["owners"]]
        joined = owners[0] if len(owners) == 1 else ", ".join(owners[:-1]) + " e " + owners[-1]
        parts.append(f"{name} pertence a {joined}.")
    n = len(mine)
    if n < cfg["min_articles"]:
        parts.append(f"Nos últimos {cfg['window_days']} dias a app guardou {n} "
                     f"{'notícia' if n == 1 else 'notícias'} — poucas para tirar tendências "
                     f"(mínimo {cfg['min_articles']}).")
        return " ".join(parts)
    parts.append(f"Nos últimos {cfg['window_days']} dias: {n} notícias.")
    by_kind = {}
    for f in data:
        by_kind.setdefault(f["kind"], []).append(f)
    if "tone" in by_kind:
        parts.append(by_kind["tone"][0]["text"])
    else:
        parts.append("O tom está em linha com a média (diferenças abaixo de "
                     f"{cfg['tone_diff_pp']} pontos).")
    if "topic" in by_kind:
        labels = [f["title"].removeprefix("Destaca ") for f in by_kind["topic"]]
        parts.append("Destaca-se por " + ", ".join(labels) + ".")
    if "silence" in by_kind:
        parts.append(f"Cobriu pouco {len(by_kind['silence'])} dos temas em destaque nos outros jornais.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Silences: topics most outlets covered and one did not
# --------------------------------------------------------------------------
def silences(conn, scope, rows=None, cfg=None):
    cfg = cfg or settings()
    rows = rows if rows is not None else load(conn, cfg["window_days"])
    # Mainstream only: a regional or independent outlet not covering a national
    # story is its editorial choice, not a silence.
    rows = [a for a in rows if a["scope"] == scope and a["group"] == "mainstream"]
    names = _source_names()
    per_outlet = Counter(a["source"] for a in rows)
    outlets = [o for o, n in per_outlet.most_common() if n >= cfg["silence_min_outlet_articles"]]
    total = sum(per_outlet[o] for o in outlets)
    labels = archive.labels(conn, scope)
    by_topic = {}
    for a in rows:
        if a["source"] in outlets:
            for slug in a["topics"]:
                by_topic.setdefault(slug, Counter())[a["source"]] += 1
    topics_out = []
    for slug, counts in by_topic.items():
        topic_total = sum(counts.values())
        covered = sum(1 for o in outlets if counts.get(o))
        if not outlets or covered / len(outlets) < cfg["silence_min_coverage"]:
            continue
        cells = {}
        for o in outlets:
            expected = per_outlet[o] * topic_total / total
            count = counts.get(o, 0)
            ratio = count / expected if expected else None
            cells[o] = {"count": count, "expected": round(expected, 1),
                        "ratio": round(ratio, 2) if ratio is not None else None,
                        "silent": expected >= cfg["silence_min_expected"]
                                  and count <= expected * cfg["silence_max_ratio"]}
        topics_out.append({"slug": slug, "label": labels.get(slug, slug.replace("-", " ")),
                           "total": topic_total, "covered_by": covered, "cells": cells})
    topics_out.sort(key=lambda t: -t["total"])
    return {"scope": scope, "window_days": cfg["window_days"],
            "outlets": [{"id": o, "name": names.get(o, o), "articles": per_outlet[o]} for o in outlets],
            "topics": topics_out,
            "thresholds": {k: cfg[k] for k in ("silence_min_outlet_articles", "silence_min_coverage",
                                                "silence_max_ratio", "silence_min_expected")}}


def silence_articles(conn, scope, slug, outlet=None, limit=50):
    """The articles behind one cell of the silences table (or the whole topic row)."""
    rows = [a for a in load(conn) if a["scope"] == scope and slug in a["topics"]
            and (outlet is None or a["source"] == outlet)]
    return [_brief(a) for a in rows[:limit]]


def silence_findings(conn, rows, ident, cfg):
    """The silences table seen from one outlet: the cells where it is silent.

    The evidence of a silence is what the other outlets published on the topic.
    """
    scope = _scope_of([a for a in rows if a["source"] == ident])
    table = silences(conn, scope, rows, cfg)
    n_outlets = len(table["outlets"])
    out = []
    for t in table["topics"]:
        cell = t["cells"].get(ident)
        if not cell or not cell["silent"]:
            continue
        others = [a for a in rows if a["scope"] == scope and t["slug"] in a["topics"]
                  and a["source"] != ident]
        out.append({"kind": "silence", "icon": "🔇", "slug": t["slug"],
                    "title": f"Pouca cobertura de «{t['label']}»",
                    "text": (f"{cell['count']} notícias, quando o seu volume faria esperar "
                             f"~{cell['expected']:.0f}; {t['covered_by']} de {n_outlets} jornais "
                             f"cobriram o tema. Pode ser do feed RSS e não do jornal."),
                    "numbers": {"count": cell["count"], "expected": cell["expected"],
                                "covered_by": t["covered_by"], "outlets": n_outlets},
                    **_evidence(others, cfg["articles_per_finding"])})
    return sorted(out, key=lambda f: -f["numbers"]["expected"])
