"""Who owns each outlet, and articles that mention their own owner.

Both come from the researched outlet profiles (``profiles/outlets/*.yaml``):

``owners``          who holds the outlet: [{name, share, kind, via, source}]
``owner_keywords``  proper names of the owner, its group, sister companies and
                    people, matched case-sensitively as whole words. Never a
                    generic word such as "Governo": it would flag half the news.
"""
from . import config, profiles
from .search import raw_searchable

AGENCIES = {"lusa", "efe", "afp", "ap", "reuters"}   # news agencies, not outlets people read
OWNER_KINDS = {"state", "public", "company", "person", "fund", "cooperative", "nonprofit", "trust"}


def outlet_profiles():
    """id -> profile of every outlet and agency with a file."""
    return {p.stem: profiles._read(p) for p in sorted((profiles.PROFILES / "outlets").glob("*.yaml"))}


def keywords_by_source():
    return {ident: [k for k in (p.get("owner_keywords") or []) if k]
            for ident, p in outlet_profiles().items()}


def mentions(raw_text, keywords):
    """Keywords found in a text, as whole words with their exact capitals and accents.

    ``raw_text`` is the padded form stored in ``articles.raw_text``.
    """
    text = raw_text or " "
    found = []
    for keyword in keywords:
        needle = raw_searchable(keyword)
        if needle.strip() and needle in text and keyword not in found:
            found.append(keyword)
    return found


def graph():
    """Owners -> outlets, for the ownership map.

    Owners with the same name are one node, so the Portuguese State links RTP
    and Lusa, and Shifter Generation links Shifter and LPP.
    """
    names = {s["id"]: s["name"] for s in config.load_sources(enabled_only=False)}
    enabled = {s["id"] for s in config.load_sources()}
    outlets, owners, links = [], {}, []
    for ident, p in outlet_profiles().items():
        outlets.append({"id": ident, "name": names.get(ident) or p.get("name", ident),
                        "type": p.get("type", ""), "in_app": ident in enabled,
                        "agency": ident in AGENCIES})
        for o in p.get("owners") or []:
            key = profiles.slugify(o["name"])
            node = owners.setdefault(key, {"id": key, "name": o["name"], "kind": o.get("kind"),
                                           "outlets": []})
            node["outlets"].append(ident)
            links.append({"owner": key, "outlet": ident, "share": o.get("share"),
                          "via": o.get("via"), "source": o.get("source")})
    return {"outlets": outlets, "owners": list(owners.values()), "links": links}
