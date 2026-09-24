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
.venv/bin/python -m pytest -q  # 78 tests, all should pass
.venv/bin/python -m app.ingest # fetch feeds once, from the shell
.venv/bin/python -m app.topics # print the current topics per scope and group
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
| Slider mixing | `app/mixing.py` |
| API, scheduler, static files | `app/main.py` |
| Schema and migrations | `app/db.py` |
| Page | `static/` |

Editable data files: `lexicon/custom_*.txt`, `lexicon/rules_*.txt`,
`config/stoplist.txt`, `config/synonyms.txt`. They are the intended place to
fix wrong labels or noisy topics — prefer them over changing code.

## Decisions worth keeping

- **The slider is a hard constraint.** The requested share is always honoured:
  when one side runs short the feed gets *shorter*, never padded from the other
  side. Asking 100% positive shows only positive articles, even if that means 44
  instead of 100. A selection with no positive articles shows an empty feed at
  any setting above 0%. This replaced padding, which was misleading.
- **Word lists beat code changes.** Two lexicons (SentiLex-PT02 for Portuguese,
  VADER for English) plus custom lists that override them, plus co-occurrence
  rules in `lexicon/rules_*.txt` for headlines where single words mislead
  ("Euribor ultrapassa 3%" is bad news; "desemprego desce" is good news). A rule
  silences the words it names. Contradictory rules on the same anchor cancel.
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
- Several feeds need care: CNN Portugal double-escapes HTML, WordPress feeds
  append "O conteúdo … apareceu primeiro em …" footers, Divergente's real
  articles are only in `?post_type=trabalho`, and DN sends no summaries.
- SIC Notícias, Jornal de Notícias, Reuters and Associated Press have no working
  public feed (checked September 2026). They sit in `sources.yaml` with
  `enabled: false` and a comment. Do not scrape them.

## Conventions

- Verify feeds and claims by running them, not from memory.
- Every behaviour change gets a test; `tests/` mirrors the modules.
- When sentiment or topics look wrong, check `matched_words` (the badge tooltip
  shows it) and fix the data file, not the algorithm.
