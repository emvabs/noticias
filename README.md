# Notícias: agregador com controlo de positividade

This is a local website that gathers the latest news from Portuguese and international outlets. A **Portugal | Mundo | Independentes** switch picks what you read, a search box and the trending topics narrow the feed, and the **Tom** control sets what share of the feed is positive news. Everything runs on your own computer: no hosting, no accounts, no paid APIs.

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

Open **http://localhost:8000** (it works the same on a phone, tablet or large monitor). On startup the server fetches every feed in the background, then again every 20 minutes. The refresh button (↻) in the top bar triggers a fetch right away; hovering it, or the **⋯** menu, says when the last one was.

## How it works

| Part | File |
|---|---|
| Outlets / RSS feeds (with `scope`, `language`, `group`) | `sources.yaml` |
| Settings (port, interval, thresholds, retention) | `config.yaml` |
| Feed ingestion, dedupe, 7-day cleanup | `app/ingest.py` |
| Word-list scorer | `app/sentiment.py` |
| Topic extraction | `app/topics.py` |
| Story clustering | `app/clusters.py` |
| Topic noise words (editable) | `config/stoplist.txt` |
| Combination rules (editable) | `lexicon/rules_pt.txt`, `lexicon/rules_en.txt` |
| Search matching and synonyms | `app/search.py`, `config/synonyms.txt` |
| Slider mixing logic | `app/mixing.py` |
| API + scheduler | `app/main.py` |
| Page | `static/` |
| Database | `data/news.db` (SQLite; delete it to start fresh) |

### The page

One row is pinned at the top: the brand, the sections (**Notícias · Resumo · Guardados · Investigar**, a tab bar at the bottom on phones), the search box, the **Tom** button, refresh and the **⋯** menu (articles per page, light/dark theme, links). Under it, a row that scrolls away holds the scope switch, the trending topics and the **Jornais** menu. Every menu is a small box under its button on a computer and a bottom sheet on a phone; Esc or a click outside closes it.

### Reading the feed

- **Two views**, chosen in **⋯ → Vista**: *Cartões* (headline, summary, topics) or *Lista* (one dense row per article: outlet, time, headline). The choice is remembered.
- **Time groups**: "Últimas horas" (the last 3 hours, with "há 40 min"), "Hoje", "Ontem", then the date; past the first group each article shows its hour.
- **Tone** is a small dot at the end of the meta line: green positive, red negative, a ring for neutral. Hover or tap it to see the score and the words that decided it.
- **New since your last visit**: articles *fetched* after you last left the page carry a "nova" tag, and a line above the list counts them, with **Ver só as novas** to show only those. A visit counts once the page has been open 20 seconds, so a reload does not reset it; coming back to the tab after 5 minutes starts a new one.
- **Already read**: headlines you opened are dimmed.

New and read are remembered in this browser only (`localStorage`), per device; nothing is sent to the server.

### Agora (the side column)

On a wide screen (≥ 1200px) a column beside the feed shows:

- how many articles are new since your last visit, with a button to show only those;
- **the tone of the last 24 hours** in the current selection (Portugal, Mundo or Independentes) — every article of the day, before the slider;
- **the silence of the day**: among the topics trending now, the mainstream outlet furthest below what its volume predicts (from the same table as *Silêncios*, with the same caveat: the gap may be the feed's);
- links into Investigar.

On narrower screens the same box sits at the top of Investigar. API: `GET /api/today?scope=portugal&group=mainstream`.

### Stories: one item per story

When several outlets publish the same story, the feed shows it once — the newest version — with a **+3 jornais** button that unfolds **Como os outros jornais titularam**: each outlet's headline, tone dot and time, side by side. The story told by the most outlets opens the page, larger and unfolded (**Em destaque · contada por 5 jornais**).

How articles are grouped (`app/clusters.py`, recomputed after every fetch): within each scope's last 48 hours, every article becomes a bag of its meaningful words (stopwords, outlet names and `config/stoplist.txt` removed, plurals merged, headline words counted double, names a little more), weighted by TF-IDF. Two articles are linked when their similarity reaches `clusters.min_similarity` (0.35) **and** they share at least two words — one shared word is usually a headline formula ("O que se sabe sobre…"). Links merge strongest first, and two groups only merge while the average similarity between all their members stays above `min_group_similarity`, so one vague word cannot chain unrelated stories together; no group grows past `max_size`. No ML: plain word counts.

**The slider still decides.** Each side (positive / the rest) keeps one article per story, so 50 means 50 different stories and the share counts stories. The other versions shown under a story are only those on the **same side**: at 100% positive, a negative version of the same story is never attached.

Check what is grouped right now, and tune the thresholds under `clusters:` in `config.yaml` if it joins different stories (raise `min_similarity`) or leaves the same one apart (lower it):

```bash
.venv/bin/python -m app.clusters
```

### Scope switch

Every outlet has a `scope` (`portugal` or `world`) and a `language` (`pt` or `en`) in `sources.yaml`. The switch filters by scope **before** the outlet menu and the positivity mix, so each scope has its own outlets. The choice is remembered between visits, and flipping it keeps the slider where it is.

Switching scope shows the stored articles immediately and then fetches the feeds, the same as pressing refresh — unless a fetch happened in the last minute, so toggling back and forth doesn't hit every outlet each time.

### Independentes

The third position of the scope switch shows five independent Portuguese outlets instead of the mainstream ones. The choice is remembered between visits.

While it is on, the outlet choices in the **Jornais** menu are ignored; going back to **Portugal** restores exactly the selection you had. Topics are recomputed for the independent outlets alone, and the slider keeps working unchanged.

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

Because they publish rarely, a broken feed is easy to miss, so with **Independentes** on the **Jornais** menu lists when each one last published, in red past 30 days.

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

In the page, the chips sit in the filter row, labelled **Em destaque**; the row scrolls sideways when they do not fit. They are shortcuts, not a checklist: **one topic at a time**, and clicking the selected one clears it. The selection is cleared when you switch scope, because each scope has its own topics, and also when a topic drops out of the shortlist after a recompute.

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

### Tom (the slider)

The **Tom** button in the top bar shows the share asked for (`Tom 60%`) next to a small bar of what the list actually holds. Clicking it opens the slider, whose track runs from red (negative) to green (positive), with a meter of the current list (e.g. `43 positivas · 3 neutras · 4 negativas`), so the request and the result sit side by side.

With N articles (default 50) and P %, the feed takes `round(N × P)` of the newest **positive** articles and the rest from the newest **neutral + negative** ones, then sorts everything by publish time.

**The share is always honoured.** When one side runs short, the feed gets shorter instead of being padded from the other side: at 100% you see only positive news, however few there are, and a note at the end of the list says so (`Só há 45 notícias positivas nesta seleção, por isso a lista tem 45 em vez de 100`), with a button that moves the slider to the nearest share that fills the page. The **Tom** button carries a small amber dot while the list is shorter than asked. Padding used to be the behaviour, and it was misleading — asking for 100% positive with 44 positive articles and a 100-article page gave a feed that was more than half negative.

One consequence worth knowing: if a selection has no positive articles at all, any setting above 0% shows an empty feed with a note and the same button. The slider is a hard constraint, not a preference.

Filters apply in this order: **scope → group → outlets → topic or search → positivity mix**.

That happens often with the independent outlets: investigative reporting on inequality and oppression scores negative on a word list almost by construction.

API: `GET /api/feed?scope=portugal&group=independent&positive_pct=60&limit=50&sources=publico,rtp&topics=greve&q=habitacao`
(`scope` defaults to `portugal`, `group` to `mainstream`; `topics` matches articles carrying **any** of the slugs; `q` requires every word.) Each article carries `outlets` (how many outlets tell its story) and `also` (the other versions, same side of the slider); `lead_id` names the story told by the most outlets.

Other endpoints: `GET /api/topics?scope=portugal&group=independent`, `GET /api/sources?scope=world`, `GET /api/status`, `POST /api/refresh`.

Narrow topic selections make the pools small, so the end-of-list note appears far more often — that is the slider telling you there are not enough positive (or non-positive) articles left to honour the request. When nothing matches at all, the page names the filter to loosen.

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
- Hover or tap a card's tone dot to see which words matched.
- Two kinds of word never count: the outlets' own names (SentiLex has "observador" as positive, VADER has "guardian") and the negation words themselves ("no" is negative in VADER, but here it is an operator on the words that follow).

A caveat worth knowing: some feeds (Diário de Notícias, and several outlets' briefs) carry no summary, so a single word in the headline decides the label. When you spot a wrong one, look at the tone dot's tooltip — it names the culprit — and silence it with a `0` line in the custom list, or add a combination rule.

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

Every word of a rule must appear somewhere in the headline or summary, in any order; a `*` makes a term a prefix. When a rule fires, the words it names stop counting on their own, so the rule wins over the word list, and the tooltip shows `euribor + ultrapassa` instead of the individual words.

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

### Who is behind the news

Click a card's **i** button, the outlet's name or a journalist's name to open a panel on who owns and funds the outlet and, when the byline is known, who the journalist is (on a computer it slides in from the right and leaves the feed visible; on a phone it is a bottom sheet). Nothing opens on hover. The panel starts short — owner, funding, political links and what this app has seen (how many articles, how many positive or negative) — and **Ver tudo, com as fontes** unfolds every claim with its source. Every profile shows the date it was checked, and **Investigar** goes to the outlet's or journalist's page. The name you clicked comes first.

**Where the bylines come from.** Each outlet in `sources.yaml` has an `author_from`:

| `author_from` | Meaning | Outlets |
|---|---|---|
| `feed` (default) | the RSS item carries the byline | Expresso, Observador, DN, Guardian and the independents |
| `page` | read once from the article page (JSON-LD or `<meta name="author">`) | Público, CNN Portugal, BBC, DW, Euronews, Al Jazeera |
| `none` | no byline anywhere | RTP |

Page bylines are read in the background, `fetch.author_pages_per_run` pages per update (60), newest first, so after the first start the backlog clears over a few updates.

**The profiles** are plain YAML files you can edit:

```
profiles/outlets/<id>.yaml         # one per outlet or agency (lusa, afp, efe, ap, reuters)
profiles/journalists/<slug>.yaml   # one per journalist, slug = name without accents: hugo-franco
profiles/aliases.txt               # bylines that are not people: "Agência Lusa = outlet:lusa"
```

Each claim is a `text` with its `source` (a URL or a list of them). Outlets have `owner`, `funding`, `political_links` (`official` / `reported`) and `narrative`; journalists have `role`, `career`, `political_links` and `narrative`. See any existing file for the layout. The rules the current files follow:

- no claim without a source; opinions and criticism are attributed ("segundo…", "artigo de opinião de…");
- journalists: professional record only; a political link only when documented (a party post, a candidacy, a government job, a public statement), never inferred from what they write;
- `last_checked` is updated whenever a file is reviewed.

#### Keeping the profiles up to date

Each update brings new articles. What happens on its own:

- bylines of new articles are stored (page bylines: 60 per update, newest first);
- a journalist or agency that already has a profile gets it on new articles at once;
- the "Nesta app" numbers are counted live;
- bylines that name an outlet ("Redação CNN Portugal", "BBC Africa") go to that outlet, and ones with words like "Redação", "Staff", "Agências", "Equipa" are treated as not a person — no `aliases.txt` line needed.

What is left for you, shown at the foot of the page ("N jornalistas sem perfil · N perfis a rever") and in the terminal:

```bash
.venv/bin/python -m app.profiles          # journalists with 3+ articles and no profile, then old profiles
.venv/bin/python -m app.profiles --min 1  # every journalist without a profile
```

It only reads the database, so it is safe while the server runs. For each line:

1. **Not a person** (flagged "uma só palavra" / "tudo em maiúsculas": a section, a brand, a username like `mdalmeida`) → add a line to `profiles/aliases.txt`: `Azul = -`, or `mdalmeida = journalist:maria-almeida` once you know who it is.
2. **A person** → research and create `profiles/journalists/<slug>.yaml` (the slug is printed in the list), copying the layout of an existing file. Every claim needs a `source`; `last_checked` is today.
3. **Profiles to review** (checked more than `profiles.stale_after_days` ago, 180 by default) → re-check the claims, fix what changed, update `last_checked`. Until then the box says "pode estar desatualizado".
4. Run `.venv/bin/python -m pytest -q` — it fails on any claim without a source or a file whose name does not match its slug.

No restart is needed: profiles and aliases are read on every request. The thresholds live under `profiles:` in `config.yaml`.

### Investigar (Deep Dive)

**Investigar** in the sections (or **Investigar** in the "who is behind" panel) opens a view that starts from three questions — *Quem é dono de quê?*, *Como cobre cada jornal?* and *O que ficou por noticiar?* — each leading to one of its tabs. The address follows the tab (`#investigar/jornais/publico`; old `#deep-dive/…` links still work), so the browser's back button and bookmarks work.

On an outlet's or journalist's page the summary and the findings come first, as cards; the radial map of what supports them follows, folded on phones. On a phone, once something is chosen, the list folds into a **Mudar de jornal** button.

- **Propriedade** — who owns each outlet and agency, as a two-column map (owners → outlets, with the share on each line). Shared owners are one node: the Portuguese State links RTP and Lusa. Click an outlet for its Deep Dive, an owner for what it holds and the sources.
- **Jornais / Jornalistas** — pick one; you get a short summary, the **findings**, and a radial map with the selection in the centre, the findings around it and, around each finding, the articles (coloured by tone) or research sources behind it. Drag to pan, wheel or +/− to zoom, click a finding to highlight it, click an article to open it. Each finding card also lists its articles, which is the view to use on a phone.
- **Silêncios** — trending topics × mainstream outlets: how much each outlet published on a topic compared with what its volume predicts. 🔇 marks far less than expected; click a cell to see what was (and was not) published. A silence can come from the RSS feed rather than the outlet — Público's feed shows ~10 items per request — so it is a lead, not proof. Independent outlets are left out: not covering a national story is their editorial choice.

**Findings** are of two kinds, never mixed:

| | Kind | From |
|---|---|---|
| 📋 | Researched: ownership, funding, political links, documented criticism, career | the profile files, each with its source link |
| 📊 | Tone compared with the other outlets (or, for a journalist, with the rest of their outlet) | the archive |
| 📊 | Terms in the headlines far more frequent than in the comparison | the archive |
| 📊 | Share of agency copy among articles with a known byline | the archive |
| ⚠️ | Articles that mention the outlet's own owner, group or sister company | `owner_keywords` |
| 🔇 | Widely covered topics the outlet barely covered | the archive |

Data findings need at least 15 articles in the last 90 days; below that the summary says there are too few. They describe **tendencies in the data** — the tone is measured with word lists, and a frequent term says what an outlet covers, not what it thinks. All thresholds are under `deepdive:` in `config.yaml`. Noisy terms ("Palavras Cruzadas", a recurring section) are silenced in `config/stoplist.txt`.

**⚠️ Dono on a card** means the piece names the outlet's own owner, group or a sister company (Público on Sonae, Expresso on SIC, CNN Portugal on TVI). The names are `owner_keywords` in each outlet's profile, matched as whole words with exact capitals: proper names only, never a generic word like "Governo", and never the outlet's own name ("disse ao Observador" is attribution, not a conflict).

**The archive.** The feed keeps 14 days; the Deep Dive needs months. Every article is also copied to `article_archive` (title, bylines, tone, topics, owner mentions — no summary; roughly 50 MB a year), which is never cleaned up. Topics are accumulated there, since the feed only keeps the ones trending now. The archive was filled from the articles already stored on the first start, so trends about journalists will grow more reliable over the coming weeks.

### Screen sizes and devices

Checked from a 280px foldable cover screen up to a 2560px monitor, in portrait and landscape, with no horizontal scrolling anywhere.

- **Phones** (≤ 760px): only a 54px row stays pinned (brand, search, Tom, refresh, menu); the filters scroll away with the page, and the sections move to a tab bar at the bottom. Menus open as bottom sheets over a dimmed page.
- **Narrow windows** (≤ 1000px): the search box becomes a button that opens the box over the bar.
- **Landscape phones and short windows** (height ≤ 560px): the top bar is not pinned at all, because a pinned bar would take too much of the screen.
- **Touch devices** (`pointer: coarse`): buttons and chips grow to at least 44px, and the search box uses a 16px font so iOS Safari does not zoom when tapped. The tone dot's tooltip opens on tap as well as hover, and hover effects are limited to devices that actually have a pointer.
- **Notched phones**: `viewport-fit=cover` plus `env(safe-area-inset-*)` padding, so nothing hides under a notch or home indicator in landscape.
- **Large screens**: the *Agora* column appears from 1200px; from 1800px the cards form two columns and the page widens to 1480px.
- **Theme**: follows the system unless **⋯ → Tema** forces light or dark (remembered, applied before the first paint).
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

These features add eight columns to `articles` (`scope`, `language`, `group`, `topics`, `search_text`, `raw_text`, `authors`, `cluster_id`) and the `topics_cache`, `article_archive` and `topic_labels` tables. They are created automatically on startup; scope and language are backfilled from `sources.yaml` and the two search columns are rebuilt from the stored headlines, so no manual step is needed. `cluster_id` is NULL (the article stands alone) until the next fetch groups the stories. `authors` starts empty (NULL) on old rows: it fills in when the feed lists the article again, or from the page for `author_from: page` outlets. Scope, language and group are re-synced from `sources.yaml` on every startup, so moving an outlet between groups applies to the articles already stored. **Restart the server after pulling changes**: an older running process reads the new `config.yaml`, finds no lexicon where it expects one, and would re-score every article as neutral.
