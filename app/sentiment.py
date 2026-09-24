"""Word-list sentiment scorer, one lexicon set per language.

Portuguese: SentiLex-PT02 (CC-BY 4.0, Carvalho & Silva) + lexicon/custom_pt.txt
English:    VADER lexicon (MIT, C.J. Hutto)          + lexicon/custom_en.txt

Matching is done on lowercase, accent-stripped tokens. The custom list always
wins over the base lexicon, so it can fix or silence a word.

Some headlines only make sense as a combination: "Euribor ultrapassa 3%" is bad
news although "ultrapassa" is a positive word, and "desemprego desce" is good
news although both words are negative. lexicon/rules_*.txt holds those pairs;
a matching rule replaces the polarity of the words it names.
"""
import re
import unicodedata

from . import config

TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
APOSTROPHES = str.maketrans("", "", "'’ʼ")
# VADER rates words from -4 to +4; ignore the weak ones, which are mostly noise in news.
VADER_MIN_STRENGTH = 0.5


def normalize(text):
    """Lowercase, drop apostrophes (don't -> dont) and strip accents (ç -> c)."""
    text = unicodedata.normalize("NFD", (text or "").lower().translate(APOSTROPHES))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def tokenize(text):
    return TOKEN_RE.findall(normalize(text))


def _sign(x):
    return (x > 0) - (x < 0)


def load_sentilex(path):
    """Return {normalized_word: +1/-1} from SentiLex-flex-PT02.txt.

    Idioms/multi-word entries are skipped. When a form appears several times
    (homographs, or words that collide after accent stripping), votes are
    summed; ties become neutral and are dropped.
    """
    votes = {}
    pol_re = re.compile(r"POL:N[01]=(-?\d+)")
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "PoS=IDIOM" in line or "," not in line:
                continue
            forms, _, attrs = line.partition(".PoS=")
            form = forms.split(",")[0].strip()
            if not form or " " in form:
                continue
            pols = pol_re.findall(attrs)
            if not pols:
                continue
            # Prefer the polarity towards the subject (N0); fall back to N1.
            pol = _sign(int(pols[0])) or _sign(int(pols[-1]))
            key = normalize(form)
            votes[key] = votes.get(key, 0) + pol
    return {w: _sign(v) for w, v in votes.items() if v}


def load_vader(path):
    """Return {normalized_word: +1/-1} from vader_lexicon.txt (word \\t mean \\t sd \\t ratings)."""
    lexicon = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            word = normalize(parts[0])
            if not TOKEN_RE.fullmatch(word):  # skip emoticons like :-)
                continue
            try:
                mean = float(parts[1])
            except ValueError:
                continue
            if abs(mean) >= VADER_MIN_STRENGTH:
                lexicon[word] = _sign(mean)
    return lexicon


BASE_LOADERS = {"sentilex": load_sentilex, "vader": load_vader}


def load_custom(path):
    """Parse a custom word list.

    Line format: `<word> <+1|-1|0>`; `#` starts a comment.
    A trailing `*` makes it a prefix match (e.g. `incendi* -1`).
    Weight 0 removes a word coming from the base lexicon (useful for noisy words).
    Returns (exact: dict, prefixes: list[(prefix, weight)]).
    """
    exact, prefixes = {}, []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            word, weight = normalize(parts[0]), _sign(int(parts[1]))
            if word.endswith("*"):
                prefixes.append((word[:-1], weight))
            else:
                exact[word] = weight
    prefixes.sort(key=lambda p: -len(p[0]))  # longest prefix wins
    return exact, prefixes


def outlet_words():
    """Outlet names are not sentiment: SentiLex has "observador" as positive and
    VADER has "guardian", which would tint every article carrying the byline."""
    words = set()
    for src in config.load_sources(enabled_only=False):
        for word in normalize(src["name"]).split():
            if len(word) > 2:
                words.add(word)
    return words


def load_rules(path):
    """Parse a co-occurrence rule file.

    Line format: `palavra, outra  <+1|-1>` - every term must appear in the text
    (in any order) for the rule to fire. A trailing `*` makes a term a prefix.
    """
    rules = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            body, _, weight = line.rpartition(" ")
            try:
                weight = _sign(int(weight))
            except ValueError:
                continue
            terms = [normalize(t.strip()) for t in body.split(",") if t.strip()]
            if len(terms) >= 2 and weight:
                rules.append({"terms": terms, "weight": weight,
                              "label": " + ".join(terms)})
    return rules


class Scorer:
    def __init__(self, base=None, custom_exact=None, custom_prefixes=None, rules=None,
                 negation_words=("nao", "nunca", "sem"), negation_window=3,
                 positive_threshold=0.2, negative_threshold=-0.2):
        self.base = base or {}
        self.rules = rules or []
        self.custom_exact = custom_exact or {}
        self.custom_prefixes = custom_prefixes or []
        self.negation_words = {normalize(w) for w in negation_words}
        self.negation_window = negation_window
        self.pos_th = positive_threshold
        self.neg_th = negative_threshold

    def polarity(self, token):
        """Return (weight, source) for one token, or (0, None)."""
        candidates = [token]
        if "-" in token:  # clitics/compounds: "matou-se" -> "matou"
            candidates.append(token.split("-")[0])
        for t in candidates:
            if t in self.custom_exact:
                return self.custom_exact[t], "custom"
            for prefix, w in self.custom_prefixes:
                if t.startswith(prefix):
                    return w, "custom"
            if t in self.base:
                return self.base[t], "base"
        return 0, None

    def label_for(self, score):
        if score > self.pos_th:
            return "positive"
        if score < self.neg_th:
            return "negative"
        return "neutral"

    def _matches(self, term, tokens):
        """Does a rule term (word or `prefix*`) appear among the tokens?"""
        if term.endswith("*"):
            prefix = term[:-1]
            return any(t.startswith(prefix) for t in tokens)
        return term in tokens

    def apply_rules(self, tokens, matched):
        """Replace word hits with rule hits where a rule fires.

        A rule that fires silences the words it names, so "ultrapassa" no longer
        counts as positive in "Euribor ultrapassa 3%".
        """
        fired = [rule for rule in self.rules
                 if all(self._matches(term, tokens) for term in rule["terms"])]

        # Rules ignore word order, so a headline like "sobem os preços, descem
        # os consumos" fires both directions for the same anchor word. That is
        # genuinely ambiguous, so neither side counts.
        by_anchor = {}
        for rule in fired:
            by_anchor.setdefault(rule["terms"][0], []).append(rule)
        fired = [rule for rules in by_anchor.values()
                 if len({r["weight"] for r in rules}) == 1
                 for rule in rules]

        for rule in fired:
            named = {term.rstrip("*") for term in rule["terms"]}
            matched = [m for m in matched
                       if not any(m["word"].startswith(n) for n in named)]
            matched.append({"word": rule["label"], "polarity": rule["weight"],
                            "negated": False, "source": "rule"})
        return matched

    def score(self, text):
        """Return (score, label, matched_words)."""
        tokens = tokenize(text)
        matched = []
        for i, tok in enumerate(tokens):
            # A negation word is an operator on the next words, not a sentiment
            # of its own; VADER scores "no" negative, which double-counted it.
            if tok in self.negation_words:
                continue
            weight, source = self.polarity(tok)
            if not weight:
                continue
            window = tokens[max(0, i - self.negation_window):i]
            negated = any(w in self.negation_words for w in window)
            if negated:
                weight = -weight
            matched.append({"word": tok, "polarity": weight,
                            "negated": negated, "source": source})

        matched = self.apply_rules(tokens, matched)
        pos = sum(1 for m in matched if m["polarity"] > 0)
        neg = sum(1 for m in matched if m["polarity"] < 0)
        score = (pos - neg) / (pos + neg + 1)
        return round(score, 4), self.label_for(score), matched


DEFAULT_LANGUAGE = "pt"
_cache = {}


def language_config(language):
    cfg = config.load_config().get("sentiment", {})
    languages = cfg.get("languages", {})
    lang_cfg = languages.get(language) or languages.get(DEFAULT_LANGUAGE) or {}
    return cfg, lang_cfg


def get_scorer(language=DEFAULT_LANGUAGE):
    """Build the scorer for a language; rebuilt automatically when a lexicon file changes."""
    cfg, lang_cfg = language_config(language)
    paths = [config.path(p) for p in (lang_cfg.get("base_lexicon"), lang_cfg.get("custom"),
                                      lang_cfg.get("rules")) if p]
    key = (tuple((str(p), p.stat().st_mtime) for p in paths if p.exists()), repr(cfg))
    if _cache.get(language, {}).get("key") == key:
        return _cache[language]["scorer"]

    # Fail loudly: an empty lexicon would silently label every article "neutral".
    base_path = lang_cfg.get("base_lexicon")
    if not base_path:
        raise RuntimeError(
            f"config.yaml: falta sentiment.languages.{language}.base_lexicon "
            f"(sem léxico, todas as notícias ficariam neutras)")
    if not config.path(base_path).exists():
        raise FileNotFoundError(f"Léxico não encontrado: {base_path} (ver config.yaml)")
    loader = BASE_LOADERS[lang_cfg.get("base_format", "sentilex")]
    base = loader(config.path(base_path))
    # Outlet names are silenced first, so an explicit entry in the custom file
    # can still bring one back if you ever want it.
    exact, prefixes = {word: 0 for word in outlet_words()}, []
    if lang_cfg.get("custom") and config.path(lang_cfg["custom"]).exists():
        from_file, prefixes = load_custom(config.path(lang_cfg["custom"]))
        exact.update(from_file)
    rules = []
    if lang_cfg.get("rules") and config.path(lang_cfg["rules"]).exists():
        rules = load_rules(config.path(lang_cfg["rules"]))

    scorer = Scorer(
        base=base, custom_exact=exact, custom_prefixes=prefixes, rules=rules,
        negation_words=lang_cfg.get("negation_words", ["nao", "nunca", "sem"]),
        negation_window=cfg.get("negation_window", 3),
        positive_threshold=cfg.get("positive_threshold", 0.2),
        negative_threshold=cfg.get("negative_threshold", -0.2),
    )
    _cache[language] = {"key": key, "scorer": scorer}
    return scorer
