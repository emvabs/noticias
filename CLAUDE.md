# CLAUDE.md

Context for Claude Code working on this project. `README.md` documents the app
for a person; this file records how it is built and the decisions behind it.

## What this is

A local-only news aggregator: FastAPI + SQLite + a vanilla-JS page. It fetches
Portuguese and international RSS feeds, scores each article with editable word
lists, and a slider sets how much of the feed is positive news. No hosting, no
accounts, no paid APIs, no ML.

UI text is **Portuguese**. Code, comments and docs are **English**.

## Running and testing

```bash
.venv/bin/python run.py        # http://localhost:8000, auto-reloads on edits to app/
.venv/bin/python -m pytest -q  # all should pass
.venv/bin/python -m app.ingest # fetch feeds once, from the shell
.venv/bin/python -m app.topics # print the current topics per scope and group
.venv/bin/python -m app.clusters   # recompute and print the stories told by several articles
.venv/bin/python -m app.profiles   # research to do: journalists without a profile, old profiles
.venv/bin/python scripts/evaluate.py   # sentiment accuracy, both languages
```

The Python here is the python.org build, whose SSL has no CA certificates: use
`httpx` (bundles its own) rather than `urllib`.

## Layout

| Concern | File |
|---|---|
| Outlets, feeds, `scope` / `language` / `group` | `sources.yaml` |
| Thresholds, retention, per-group settings | `config.yaml` |
| Fetch, clean, dedupe, retention | `app/ingest.py` |
| Word-list scoring | `app/sentiment.py` |
| Topic extraction (TF-IDF) | `app/topics.py` |
| Search matching, synonyms | `app/search.py` |
| Story clustering (TF-IDF cosine) | `app/clusters.py` |
| Bylines, profile resolution, local stats | `app/profiles.py` |
| Owners, owner keywords, ownership graph | `app/ownership.py` |
| Never-deleted article archive | `app/archive.py` |
| Deep Dive findings and silences | `app/deepdive.py`, `static/deepdive.js` |
| Slider mixing | `app/mixing.py` |
| API, scheduler, static files | `app/main.py` |
| Schema and migrations | `app/db.py` |
| Page | `static/` |

Editable data files: `lexicon/custom_*.txt`, `lexicon/rules_*.txt`,
`config/stoplist.txt`, `config/synonyms.txt`, `profiles/**/*.yaml`,
`profiles/aliases.txt`. They are the intended place to
fix wrong labels or noisy topics — prefer them over changing code.

## Decisions worth keeping

- **The slider is a hard constraint.** The requested share is always honoured:
  when one side runs short the feed gets *shorter*, never padded from the other
  side. Asking 100% positive shows only positive articles, even if that means 44
  instead of 100. A selection with no positive articles shows an empty feed at
  any setting above 0%. This replaced padding, which was misleading.
- **One item per story, and the slider still decides.** `mixing.collapse()`
  runs on each side separately, so `also` never crosses sides and the share
  counts stories. Pools are read `limit * 3 + 50` deep before collapsing.
  Clusters need cosine ≥ `min_similarity` *and* two shared terms; groups
  merge only while average linkage holds (no chaining). Tune `clusters:` in
  `config.yaml` with `python -m app.clusters`, not the code.
- **Word lists beat code changes.** Two lexicons (SentiLex-PT02 for Portuguese,
  VADER for English) plus custom lists that override them, plus co-occurrence
  rules in `lexicon/rules_*.txt` for headlines where single words mislead
  ("Euribor ultrapassa 3%" is bad news; "desemprego desce" is good news). A rule
  silences the words it names. Contradictory rules on the same anchor cancel.
- **One dictionary word is not enough for "positive".** A base-lexicon hit
  (SentiLex/VADER) counts 1, a custom word or rule counts 2, and positive needs
  2 (`sentiment.min_evidence`). 19% of positives rested on one generic word in
  the summary ("protocolos adequados" under a hacking headline). Negative is
  not gated: the slider only splits positive from the rest, and gating
  negative lost as many right labels as wrong ones.
- **Outlet names are never sentiment and never topics.** SentiLex scores
  "observador" positive and VADER scores "guardian" positive, which tinted every
  article carrying the byline. Names come from `sources.yaml` automatically.
- **Negation words are operators, not sentiment.** "no" is negative in VADER;
  counting it twice was a bug.
- **Search matches whole words**, with acronyms (≤5 letters, capitals) matched
  case-sensitively against `raw_text`. Without that, accent-stripping made "IA"
  match the very common Portuguese word "aí".
- **Topics are compared against the rest of the scope**, not only the group's own
  history. A slow-publishing group has no history, TF-IDF goes flat, and
  ordinary words win.
- **Group-aware settings** live under `groups.*` in `config.yaml`. The
  independent outlets publish a few pieces a month, so they get 120-day
  retention, no ingest date cutoff, and a 14-day topic window.

- **Profiles are researched by hand, never fetched at run time.** Each claim
  carries its source URL; criticism is attributed, not stated. Journalists get
  their professional record only, and political links only when documented
  (party post, candidacy, government job, own public statement) — never
  inferred from their writing. No claim without a source: when in doubt, leave
  it out and let the box say "Sem ligações públicas conhecidas".
- **Bylines are stored as cleaned names and resolved at read time**
  (`profiles.Resolver`), so editing `aliases.txt` or adding a profile applies
  to stored articles. `authors` is NULL until looked up, `[]` when there is none.
  Bylines naming an outlet, or with words such as "Redação"/"Staff"/"Agências"
  (`NON_PERSON_WORDS`), are not people even without an alias; aliases win.
  Guardian bylines carry places and job titles ("X in Kyiv", "X Senior
  correspondent"); Portuguese "e" is only a separator when both sides are full
  names ("Pedro Adão e Silva" is one person).

- **The Deep Dive reads the archive, the feed reads `articles`.** `archive.sync`
  runs twice per fetch (before cleanup, after topics) and unions topics, because
  `articles.topics` is reset on every recompute. Findings are either researched
  (profile + source URL) or data (archive + supporting articles); one with
  neither is dropped. Wording is "tendência", never "viés".
- **Investigar leads with questions and findings; graphs come after**, folded
  on phones. The side column ("Agora", `/api/today`) is rendered twice by
  `renderNow()`: beside the feed from 1200px, at the top of Investigar below.
- **Graphs are hand-drawn SVG, no D3.** Both have a fixed natural shape (radial;
  owners → outlets), which reads better than a force layout and needs no
  dependency. Pan/zoom is `panZoom()` in `static/deepdive.js`.
- **Silences compare mainstream outlets only**, and say the gap may be the
  feed's (Público's feed has ~10 items).

- **Design: modern editorial.** Warm paper, serif headlines (system serif, no
  web fonts: the app stays local), colour only where it carries meaning (tone,
  warnings, selection). Everything reads from the tokens at the top of
  `static/style.css` (surfaces, ink, meaning colours, shadows, radii, type
  scale, motion) with a dark-mode set; add tokens rather than raw values.
  Icons are line SVGs from `icon()` in `static/app.js` (`<span data-icon>`
  placeholders in the HTML, CSS masks for pseudo-elements) — no emoji in the UI.
  Dark tokens live twice: under `prefers-color-scheme` guarded by
  `:root:not([data-theme="light"])`, and under `:root[data-theme="dark"]` for
  the menu's forced theme — edit both.
- **One pinned row, controls in popovers.** The slider lives in the "Tom"
  popover; the feed keeps the screen. Menus use `popovers.bind()` (one open
  at a time, a bottom sheet on phones). Never put `backdrop-filter` on an
  ancestor of a sheet: it becomes the containing block of `position: fixed`
  (the top bar's blur is on `::before` for that reason).
- **Views are hash routes** (`#noticias`, `#resumo`, `#guardados`,
  `#silenciados`, `#investigar/<tab>/<id>`), one router in `app.js`;
  `deepdive.js` exposes `ddRoute(parts)`. "Independentes" is the third
  position of the scope switch (scope `portugal`, group `independent`).
- **"Who is behind" opens on click only** (`[data-whois]` triggers: the "i"
  button and the names), never on hover: hover boxes covered the next cards.
  Short facts first, sourced claims behind "Ver tudo".
- **Per-device state stays in `localStorage`**: layout, theme, "new since"
  (`lastSeen`, compared with `fetched_at`, not `published_at`) and read
  articles (`readArticles`, keyed by URL, pruned after 15 days). Anything that
  should follow the user across devices (saved articles, muted words) goes to
  SQLite instead. The backend's finding `icon` field is
  ignored by the page, which maps each kind to a line icon.

## Gotchas

- **Restart after changing `app/`** — `run.py` auto-reloads, but a server started
  another way will silently run old code. This cost real time twice: an old
  process re-scored every article as neutral because it read the new config with
  the old code, and another ignored a new API parameter.
- The page and static files are served with `Cache-Control: no-cache` so edits
  show up on reload; without it the browser kept stale JS.
- Migrations in `app/db.py` run on every startup and re-sync `scope`,
  `language` and `group` from `sources.yaml`, so editing that file applies to
  articles already stored.
- `app.main` connects to the real database on import (tests included), and the
  migration writes. Keep migrations cheap: the archive backfill only runs when
  the archive is empty.
- Never hold a SQLite write transaction across a network request: the page
  byline lookup commits per article, otherwise it locks the database for the
  server's own scheduler.
- Several feeds need care: CNN Portugal double-escapes HTML, WordPress feeds
  append "O conteúdo … apareceu primeiro em …" footers, Divergente's real
  articles are only in `?post_type=trabalho`, and DN sends no summaries.
- SIC Notícias, Jornal de Notícias, Reuters and Associated Press have no working
  public feed (checked September 2026). They sit in `sources.yaml` with
  `enabled: false` and a comment. Do not scrape them.

## Conventions

- Verify feeds and claims by running them, not from memory.
- Every behaviour change gets a test; `tests/` mirrors the modules.
- When sentiment or topics look wrong, check `matched_words` (the tone dot's
  tooltip shows it) and fix the data file, not the algorithm.
