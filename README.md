# Notícias: agregador com controlo de positividade

This is a local website that gathers the latest news from Portuguese and international outlets. A **Portugal | Mundo** switch picks the scope, a search box and six trending topics narrow the feed, and a slider sets what share of the feed is positive news. Everything runs on your own computer: no hosting, no accounts, no paid APIs.

## Setup (once)

Requires Python 3.10+.

```bash
cd News
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python run.py
```

The server restarts itself when you edit anything in `app/` (set `server.reload: false` in `config.yaml` to turn that off).

Open **http://localhost:8000** (it works the same on a phone, tablet or large monitor). On startup the server fetches every feed in the background, then again every 20 minutes. The **Atualizar** button triggers a fetch right away.

## How it works

| Part | File |
|---|---|
| Outlets / RSS feeds (with `scope`, `language`, `group`) | `sources.yaml` |
| Settings (port, interval, thresholds, retention) | `config.yaml` |
| Feed ingestion, dedupe, 7-day cleanup | `app/ingest.py` |
| Word-list scorer | `app/sentiment.py` |
| Topic extraction | `app/topics.py` |
| Topic noise words (editable) | `config/stoplist.txt` |
| Combination rules (editable) | `lexicon/rules_pt.txt`, `lexicon/rules_en.txt` |
| Search matching and synonyms | `app/search.py`, `config/synonyms.txt` |
| Slider mixing logic | `app/mixing.py` |
| API + scheduler | `app/main.py` |
| Page | `static/` |
| Database | `data/news.db` (SQLite; delete it to start fresh) |

### Scope switch

Every outlet has a `scope` (`portugal` or `world`) and a `language` (`pt` or `en`) in `sources.yaml`. The switch in the top bar filters by scope **before** the outlet toggles and the positivity mix, so each scope has its own outlet chips. The choice is remembered between visits, and flipping it keeps the slider where it is.

Switching scope shows the stored articles immediately and then fetches the feeds, the same as pressing **Atualizar** — unless a fetch happened in the last minute, so toggling back and forth doesn't hit every outlet each time.

### Independentes

A button at the start of the outlet row switches the feed to five independent Portuguese outlets. It is off by default, only appears in the **Portugal** position, and its state is remembered between visits.

While it is on, the mainstream outlet toggles are greyed out and ignored; turning it off restores exactly the selection you had. Topics are recomputed for the independent outlets alone, and the slider keeps working unchanged.

| Outlet | Feed | Notes |
|---|---|---|
| Fumaça | `fumaca.pt/feed/` | Podcast feed: long HTML descriptions, cut to 300 characters |
| Divergente | `divergente.pt/feed/?post_type=trabalho` | Their main feed only holds WordPress's default "Olá, mundo!" post; the reporting lives in the `trabalho` post type |
| Shifter | `shifter.pt/feed/` | |
| Lisboa Para Pessoas | `lisboaparapessoas.pt/feed/` | |
| oRegiões | `oregioes.pt/feed/` | |

These outlets publish a few pieces a week, sometimes a month, so `config.yaml` gives the group its own settings under `groups.independent`:

```yaml
groups:
  independent:
    retention_days: 120     # 14 days would leave the section almost empty
    ingest_window_days: 0   # 0 = no date cutoff, take the latest items however old
    topics:
      window_hours: 336     # 14 days instead of 48 hours
      max_topics: 8
      min_articles: 2
```

Because they publish rarely, a broken feed is easy to miss, so a line under the row shows when each one last published, turning red past 30 days.

Feed footers that WordPress appends ("O conteúdo … apareceu primeiro em …", "The post … appeared first on …") are stripped on ingestion: they were dominating the topics and tinting the sentiment score.

### Topics

Topics are not a fixed list: they are recomputed from the database after every fetch, separately for each scope.

- **Window**: articles from the last 48 hours (14 days for the independent group). Single words, word pairs and capitalized phrases ("Banco de Portugal") are counted.
- **Ranking**: TF-IDF against everything else in the scope — the group's own older pieces plus the other group's articles. A slow-publishing group has no history of its own, and without that baseline TF-IDF goes flat and ordinary words like "durante" come out on top. Proper nouns get a boost; plain word pairs do not.
- **Cleanup**: Portuguese and English stopwords, the outlets' own names, and `config/stoplist.txt` are removed. Singular/plural forms merge, as do terms appearing in more than 80% of the same articles.
- **Result**: the top 6, each with a slug, a label and an article count, cached in the `topics_cache` table. Articles in the window are tagged in their `topics` column.

Check the current output at any time:

```bash
python -m app.topics
```

Topics are noisy by nature. If something generic keeps showing up, add it to `config/stoplist.txt` (one word per line) and press **Atualizar**. The file already removes role words like "presidente" and "governo" — delete those lines if you would rather see them as topics.

In the page, the six chips sit under the outlet filters beside the search box, labelled **Em destaque**. They are shortcuts, not a checklist: **one topic at a time**, and clicking the selected one clears it. The selection is cleared when you switch scope, because each scope has its own topics, and also when a topic drops out of the shortlist after a recompute.

Topic extraction is a word-count heuristic, so it will sometimes surface something odd. The search box is there for everything the shortlist misses.

### Search

Type in the box to filter the feed, with a 250 ms debounce (Enter searches immediately, **×** clears). Searching and picking a topic are mutually exclusive: doing one clears the other, so it is always clear what the feed is showing.

**Whole words, not fragments.** "IA" does not match "praia" or "mais". Each article stores `search_text` — title + summary, lowercased, accent-free, punctuation turned into spaces and padded with a space at each end — and that padding is what makes word matching possible with plain SQL. Words longer than three letters may match a suffix, so "incendio" still finds "incêndios". A multi-word query requires all of the words, anywhere in the headline or summary.

**Acronyms are case-sensitive.** Stripping accents turns "aí" into "ai", so "IA"/"AI" used to match every article containing the very common Portuguese word "aí". Terms of up to five letters written in capitals — either by you in the box or in `config/synonyms.txt` — are matched against a second column, `raw_text`, which keeps the original capitals and accents. Typing "ia" in lowercase still works: the synonym group carries the capitalization.

**Synonyms** live in `config/synonyms.txt`, one group per line:

```
IA, AI, inteligência artificial, machine learning, chatgpt, openai
habitação, casas, arrendamento, rendas, housing
```

Searching any term in a group finds all of them, and the page shows which extra terms were searched. Terms in CAPITALS are treated as acronyms; lowercase terms match the word in any case. The file is read again whenever it changes, so no restart is needed.

### Slider

The label reads **Positivas vs Negativas**, and the track runs from red on the left to green on the right. Under it, a meter shows what the feed actually contains right now (e.g. `43 positivas · 3 neutras · 4 negativas`), so the request and the result sit side by side.

With N articles (default 50) and P %, the feed takes `round(N × P)` of the newest **positive** articles and the rest from the newest **neutral + negative** ones, then sorts everything by publish time.

**The share is always honoured.** When one side runs short, the feed gets shorter instead of being padded from the other side: at 100% you see only positive news, however few there are, and the note says `Só há 45 notícias positivas nesta seleção — a mostrar 45 de 100`. Padding used to be the behaviour, and it was misleading — asking for 100% positive with 44 positive articles and a 100-article page gave a feed that was more than half negative.

One consequence worth knowing: if a selection has no positive articles at all, any setting above 0% shows an empty feed with a note pointing at the slider. The slider is a hard constraint, not a preference.

Filters apply in this order: **scope → group → outlets → topic or search → positivity mix**.

When the requested and achieved percentages differ by more than 20 points, the indicator explains itself ("Poucas notícias positivas nesta seleção") instead of only showing two numbers. That happens often with the independent outlets: investigative reporting on inequality and oppression scores negative on a word list almost by construction.

API: `GET /api/feed?scope=portugal&group=independent&positive_pct=60&limit=50&sources=publico,rtp&topics=greve&q=habitacao`
(`scope` defaults to `portugal`, `group` to `mainstream`; `topics` matches articles carrying **any** of the slugs; `q` requires every word.)

Other endpoints: `GET /api/topics?scope=portugal&group=independent`, `GET /api/sources?scope=world`, `GET /api/status`, `POST /api/refresh`.

Narrow topic selections make the pools small, so the `Pedido X% · Real Y%` indicator appears far more often — that is the slider telling you there are not enough positive (or non-positive) articles left to honour the request. When nothing matches at all, the page names the filter to loosen.

### Scoring

- Text = title + summary, lowercased, accents stripped, split into words.
- The lexicon set is chosen by the article's language (`config.yaml` → `sentiment.languages`):

  | Language | Base lexicon | Custom list | Negation |
  |---|---|---|---|
  | `pt` | SentiLex-PT02 | `lexicon/custom_pt.txt` | não, nunca, sem |
  | `en` | VADER | `lexicon/custom_en.txt` | no, not, without, never, contractions |

- The custom list always takes priority over the base lexicon.
- **Combination rules** (`lexicon/rules_pt.txt`, `lexicon/rules_en.txt`) handle headlines where single words mislead.
- Negation: one of those words up to 3 words before a sentiment word flips its polarity.
- `score = (pos − neg) / (pos + neg + 1)`. The label is positive if > 0.2, negative if < −0.2, otherwise neutral (set in `config.yaml`).
- Hover a card's badge to see which words matched.
- Two kinds of word never count: the outlets' own names (SentiLex has "observador" as positive, VADER has "guardian") and the negation words themselves ("no" is negative in VADER, but here it is an operator on the words that follow).

A caveat worth knowing: some feeds (Diário de Notícias, and several outlets' briefs) carry no summary, so a single word in the headline decides the label. When you spot a wrong one, look at the badge tooltip — it names the culprit — and silence it with a `0` line in the custom list, or add a combination rule.

### Combination rules

Some headlines only make sense as a combination:

- *"Taxa Euribor ultrapassa 3%"* — bad news, although "ultrapassa" (to surpass) is a positive word in SentiLex.
- *"Desemprego desce"* — good news, although both words are negative on their own.

`lexicon/rules_*.txt` holds those pairs, one per line:

```
euribor, ultrapassa  -1
desemprego, desce     1
mortes, descem        1
```

Every word of a rule must appear somewhere in the headline or summary, in any order; a `*` makes a term a prefix. When a rule fires, the words it names stop counting on their own, so the rule wins over the word list, and the badge tooltip shows `euribor + ultrapassa` instead of the individual words.

Because word order is ignored, a headline can fire rules in both directions — *"sobem os preços, descem os consumos"*. When rules sharing the same first word disagree, none of them counts and the article stays neutral, rather than picking a side at random.

### Tuning the word list

Edit `lexicon/custom_pt.txt` (Portuguese) or `lexicon/custom_en.txt` (English). One `word weight` pair per line:

```
incendi*   -1     # prefix: incêndio, incêndios, incendiário…
vitoria     1     # accents are ignored
livro       0     # 0 = ignore a word that SentiLex gets wrong for news
```

Then measure the effect:

```bash
python scripts/evaluate.py          # both languages, separately
python scripts/evaluate.py en       # only English
python scripts/evaluate.py --all    # also lists correct ones
```

The test headlines are in `eval/headlines.csv` (Portuguese, 43) and `eval/headlines_en.csv` (English, 37), and you can add your own. Current accuracy: **98%** and **100%** — optimistic, since both lists were tuned against those same headlines. Clicking **Atualizar** (or restarting) re-scores every stored article with the current lists.

### Screen sizes and devices

Checked from a 280px foldable cover screen up to a 2560px monitor, in portrait and landscape, with no horizontal scrolling anywhere.

- **Phones**: the outlet and topic chips scroll away with the page while the header (brand, scope, slider) stays pinned, so the feed keeps the screen. On a 320px phone the pinned header is about 30% of the height; on a modern phone, 26%.
- **Landscape phones and short windows** (height ≤ 560px): the header is not pinned at all and the controls tighten, because a pinned bar would take two thirds of the screen.
- **Touch devices** (`pointer: coarse`): buttons and chips grow to at least 44px, and the "N notícias" select uses a 16px font so iOS Safari does not zoom when tapped. The sentiment badge's tooltip opens on tap as well as hover, and hover effects are limited to devices that actually have a pointer.
- **Notched phones**: `viewport-fit=cover` plus `env(safe-area-inset-*)` padding, so nothing hides under a notch or home indicator in landscape.
- **Large screens** (≥ 1280px): the feed becomes two columns, and the page widens to 1400px beyond 1800px.
- Long words and URLs wrap instead of widening the page, `prefers-reduced-motion` is respected, and there is a print stylesheet.

### Tests

```bash
python -m pytest
```

## Sources (checked 2026-09-21)

| Outlet | Feed | Status |
|---|---|---|
| Público | `feeds.feedburner.com/PublicoRSS` (linked from publico.pt) | ✅ only ~10 items per request |
| Expresso | `feeds.feedburner.com/expresso-geral` | ✅ |
| Observador | `observador.pt/feed/` | ✅ |
| RTP Notícias | `rtp.pt/noticias/rss` | ✅ |
| CNN Portugal | `cnnportugal.iol.pt/rss.xml` (declared in the site's `<head>`) | ✅ |
| Diário de Notícias | `dn.pt/stories.rss` (where `dn.pt/feed` redirects) | ✅ titles only, no summaries |
| SIC Notícias | none | ❌ site protected by DataDome (403), no RSS found |
| Jornal de Notícias | none | ❌ `feeds.jn.pt` stopped in 2023, `jn.pt/feed` loops on redirects |

### World (checked 2026-09-23)

| Outlet | Feed | Status |
|---|---|---|
| BBC News | `feeds.bbci.co.uk/news/world/rss.xml` | ✅ |
| The Guardian | `theguardian.com/world/rss` | ✅ summaries include newsletter boilerplate |
| Deutsche Welle | `rss.dw.com/rdf/rss-en-world` | ✅ |
| Euronews | `euronews.com/rss?level=theme&name=news` | ✅ |
| Al Jazeera | `aljazeera.com/xml/rss/all.xml` | ✅ |
| Reuters | none | ❌ feeds.reuters.com retired; reuters.com returns 401 to automated requests |
| Associated Press | none | ❌ every `.rss` path returns 403 (anti-bot protection) |

To add an outlet, add a block to `sources.yaml` (with `scope` and `language`) and restart.

## Licenses

- **SentiLex-PT02**: Paula Carvalho & Mário J. Silva, **CC-BY 4.0**, https://doi.org/10.23728/b2share.93ab120efdaa4662baec6adee8e7585f
- **VADER lexicon**: C.J. Hutto, **MIT**, https://github.com/cjhutto/vaderSentiment

Articles belong to their respective outlets. Only title, summary and link are stored.

## Upgrading an existing database

These features add six columns to `articles` (`scope`, `language`, `group`, `topics`, `search_text`, `raw_text`) and a `topics_cache` table. They are created automatically on startup; scope and language are backfilled from `sources.yaml` and the two search columns are rebuilt from the stored headlines, so no manual step is needed. Scope, language and group are re-synced from `sources.yaml` on every startup, so moving an outlet between groups applies to the articles already stored. **Restart the server after pulling changes**: an older running process reads the new `config.yaml`, finds no lexicon where it expects one, and would re-score every article as neutral.
