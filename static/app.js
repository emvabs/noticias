"use strict";

const $ = (sel) => document.querySelector(sel);
const LABELS = { positive: "Positiva", neutral: "Neutra", negative: "Negativa" };
const FRESH_MS = 60_000;   // data newer than this counts as fresh (no refetch)
const OLD_DAYS = 7;             // older than this, cards show the date itself

// ---------- persisted settings (localStorage; safe if unavailable) ----------
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

const state = {
  scope: store.get("scope", "portugal"),
  topics: [],                                   // the trending shortlist for this scope
  topic: store.get("topic", null),              // one topic at a time, or none
  query: store.get("query", ""),                // free-text search
  pct: store.get("positivePct", 50),
  limit: store.get("limit", 50),
  hidden: new Set(store.get("hiddenSources", [])),
  group: store.get("group", "mainstream"),   // "independent" while the button is on
  sources: [],
  lastUpdated: null,
};

// ---------- helpers ----------
function timeAgo(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "agora mesmo";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `há ${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `há ${hours} h`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "ontem" : `há ${days} dias`;
}

function dayMonth(iso) {
  return new Date(iso).toLocaleDateString("pt-PT", { day: "numeric", month: "short", year: "numeric" });
}

function fullDate(iso) {
  return new Date(iso).toLocaleString("pt-PT", { dateStyle: "medium", timeStyle: "short" });
}

function hueFor(id) {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return hash;
}

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  Object.assign(node, attrs);
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------- slider ----------
const slider = $("#pct");
const pctValue = $("#pct-value");
const pctActual = $("#pct-actual");

function renderPct() {
  slider.value = state.pct;
  pctValue.querySelector("strong").textContent = `${state.pct}%`;
  pctValue.querySelector("span").textContent = "positivas";
}

let debounce;
slider.addEventListener("input", () => {
  state.pct = Number(slider.value);
  store.set("positivePct", state.pct);
  renderPct();
  clearTimeout(debounce);
  debounce = setTimeout(loadFeed, 150);
});

const limitSelect = $("#limit");
limitSelect.value = String(state.limit);
limitSelect.addEventListener("change", () => {
  state.limit = Number(limitSelect.value);
  store.set("limit", state.limit);
  loadFeed();
});

// ---------- scope switch (Portugal / Mundo) ----------
const scopeButtons = [...document.querySelectorAll(".scope-btn")];

function renderScope() {
  for (const btn of scopeButtons) {
    btn.setAttribute("aria-pressed", String(btn.dataset.scope === state.scope));
  }
}

for (const btn of scopeButtons) {
  btn.addEventListener("click", async () => {
    if (btn.dataset.scope === state.scope) return;
    state.scope = btn.dataset.scope;      // the slider value is kept
    store.set("scope", state.scope);
    if (state.scope !== "portugal") setGroup("mainstream");   // no independents there
    setTopic(null);                       // topics differ per scope
    setQuery("", { render: true });
    renderScope();
    showSkeletons();
    await Promise.all([loadSources(), loadTopics()]);   // both belong to one scope
    await loadFeed();                     // show what we already have, right away
    // Then fetch the feeds, unless that just happened (toggling back and forth
    // shouldn't hit every outlet again).
    if (!state.lastUpdated || Date.now() - new Date(state.lastUpdated).getTime() > FRESH_MS) {
      await refresh();
    }
  });
}

// ---------- source filters ----------
function outletChips() {
  return state.sources.filter((s) => (s.group || "mainstream") === "mainstream");
}

function independentSources() {
  return state.sources.filter((s) => s.group === "independent" && s.enabled);
}

function activeSources() {
  return outletChips().filter((s) => s.enabled && !state.hidden.has(s.id)).map((s) => s.id);
}

const independentOn = () => state.group === "independent";

function setGroup(group) {
  state.group = group;
  store.set("group", group);
}

function renderSources() {
  const nav = $("#sources");
  nav.replaceChildren();
  const enabled = outletChips().filter((s) => s.enabled);
  const anyHidden = enabled.some((s) => state.hidden.has(s.id));

  // The button only makes sense for Portugal: these are all Portuguese outlets.
  if (state.scope === "portugal") {
    const toggle = el("button", { type: "button", className: "chip chip-group" });
    toggle.append(el("span", { className: "dot", ariaHidden: "true" }, "◆"), "Independentes");
    toggle.setAttribute("aria-pressed", String(independentOn()));
    toggle.title = independentOn()
      ? "A mostrar só jornalismo independente — clique para voltar aos generalistas"
      : "Mostrar só Fumaça, Divergente, Shifter, Lisboa Para Pessoas e oRegiões";
    toggle.addEventListener("click", async () => {
      setGroup(independentOn() ? "mainstream" : "independent");
      state.topic = null;                    // topics belong to the group
      store.set("topic", null);
      showSkeletons();
      await Promise.all([loadSources(), loadTopics()]);
      await loadFeed();
    });
    nav.append(toggle, el("span", { className: "chips-divider", ariaHidden: "true" }));
  }

  for (const s of outletChips()) {
    const chip = el("button", { type: "button", className: "chip" }, s.name);
    if (!s.enabled) {
      chip.disabled = true;
      chip.title = "Sem feed RSS disponível (ver sources.yaml)";
    } else if (independentOn()) {
      // While the button is on the individual outlets are ignored, but the
      // selection is kept so turning it off restores exactly what was there.
      chip.setAttribute("aria-disabled", "true");
      chip.setAttribute("aria-pressed", "false");
      chip.title = "Desligue “Independentes” para filtrar por jornal";
    } else {
      chip.setAttribute("aria-pressed", String(!state.hidden.has(s.id)));
      chip.title = `${s.articles} notícias guardadas`;
      if (s.errors.length) {
        chip.append(el("span", { className: "warn", ariaHidden: "true" }, "⚠"));
        chip.title += `\nErro na última atualização:\n${s.errors.join("\n")}`;
      }
      chip.addEventListener("click", () => {
        state.hidden.has(s.id) ? state.hidden.delete(s.id) : state.hidden.add(s.id);
        store.set("hiddenSources", [...state.hidden]);
        renderSources();
        loadFeed();
      });
    }
    nav.append(chip);
  }
  renderSourcesNote();
  if (anyHidden && !independentOn()) {
    const all = el("button", { type: "button", className: "chip chip-all" }, "Mostrar todos");
    all.addEventListener("click", () => {
      state.hidden.clear();
      store.set("hiddenSources", []);
      renderSources();
      loadFeed();
    });
    nav.append(all);
  }
}

const sourcesNote = $("#sources-note");

function renderSourcesNote() {
  // These outlets publish rarely, so "last piece" is the only way to notice a
  // feed that quietly stopped working.
  const outlets = independentSources();
  sourcesNote.replaceChildren();
  sourcesNote.hidden = !independentOn() || !outlets.length;
  if (sourcesNote.hidden) return;
  sourcesNote.append(el("span", {}, "Última peça:"));
  for (const s of outlets) {
    const age = s.last_published
      ? Math.floor((Date.now() - new Date(s.last_published).getTime()) / 86400000) : null;
    const text = age === null ? "sem artigos"
      : age === 0 ? "hoje" : age === 1 ? "ontem" : `há ${age} dias`;
    const item = el("span", { className: age === null || age > 30 ? "stale" : "" },
      `${s.name}: ${text}`);
    if (s.errors.length) item.title = s.errors.join("\n");
    sourcesNote.append(item);
  }
}

async function loadSources() {
  // Always the whole scope: the row shows the mainstream outlets (greyed out
  // while "Independentes" is on) and the note below lists the independents.
  const res = await fetch(`/api/sources?scope=${state.scope}`);
  state.sources = await res.json();
  renderSources();
}

// ---------- topic filter ----------
const topicsNav = $("#topics");

function setTopic(slug) {
  state.topic = slug;
  store.set("topic", slug);
  renderTopics();
}

function renderTopics() {
  topicsNav.replaceChildren(el("span", { className: "topics-title" }, "Em destaque"));
  if (!state.topics.length) {
    topicsNav.append(el("span", { className: "topics-empty" },
      "sem temas ainda — calculados a partir das últimas 48 horas"));
    return;
  }
  for (const t of state.topics) {
    const chip = el("button", { type: "button", className: "topic-chip" }, t.label);
    chip.setAttribute("aria-pressed", String(state.topic === t.slug));
    chip.append(el("span", { className: "count" }, String(t.count)));
    // One topic at a time: clicking another replaces it, clicking it again clears.
    // The current selection is read on click, never captured when rendering.
    chip.addEventListener("click", () => {
      setQuery("", { render: true });          // a topic and a search would fight
      setTopic(state.topic === t.slug ? null : t.slug);
      loadFeed();
    });
    topicsNav.append(chip);
  }
}

async function loadTopics() {
  const res = await fetch(`/api/topics?scope=${state.scope}&group=${state.group}`);
  state.topics = await res.json();
  // The shortlist is recomputed on every fetch, so a chosen topic can vanish.
  if (state.topic && !state.topics.some((t) => t.slug === state.topic)) {
    state.topic = null;
    store.set("topic", null);
  }
  renderTopics();
}

// ---------- search ----------
const searchInput = $("#search");
const searchClear = $("#search-clear");
const searchHint = $("#search-hint");

function renderSearchHint(data) {
  const also = (data && data.also_searched) || [];
  searchHint.hidden = !also.length;
  if (!also.length) return;
  searchHint.replaceChildren("Também a procurar por ", el("b", {}, also.join(", ")));
}

function setQuery(value, { render = false } = {}) {
  state.query = value;
  store.set("query", value);
  searchClear.hidden = !value;
  if (!value) searchHint.hidden = true;
  if (render) searchInput.value = value;
}

let searchDebounce;
searchInput.addEventListener("input", () => {
  setQuery(searchInput.value);
  if (state.topic) setTopic(null);        // typing replaces the topic filter
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(loadFeed, 250);
});

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();                  // Enter searches without reloading the page
  clearTimeout(searchDebounce);
  searchInput.blur();
  loadFeed();
});

searchClear.addEventListener("click", () => {
  setQuery("", { render: true });
  searchInput.focus();
  loadFeed();
});

// ---------- feed ----------
const feedList = $("#feed");
const skeletonTpl = $("#skeleton-tpl");

function showSkeletons(n = 6) {
  feedList.setAttribute("aria-busy", "true");
  feedList.replaceChildren();
  for (let i = 0; i < n; i++) feedList.append(skeletonTpl.content.firstElementChild.cloneNode(true));
}
const message = $("#message");
const tpl = $("#card-tpl");

function tooltipFor(article) {
  const tip = document.createDocumentFragment();
  tip.append(el("strong", {}, `Score ${article.score > 0 ? "+" : ""}${article.score.toFixed(2)}`));
  tip.append(el("br"));
  if (!article.matched_words.length) {
    tip.append("Nenhuma palavra do léxico encontrada.");
    return tip;
  }
  article.matched_words.forEach((m, i) => {
    if (i) tip.append(", ");
    const text = (m.negated ? "¬" : "") + m.word + (m.polarity > 0 ? " +" : " −");
    tip.append(el("span", { className: m.polarity > 0 ? "w-pos" : "w-neg" }, text));
  });
  if (article.matched_words.some((m) => m.negated)) {
    tip.append(el("br"), el("small", {}, "¬ = polaridade invertida por uma negação antes da palavra"));
  }
  return tip;
}

function renderFeed(data) {
  feedList.replaceChildren();
  for (const a of data.articles) {
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.classList.add(a.label);
    card.querySelector(".source-name").textContent = a.source_name;
    card.style.setProperty("--hue", hueFor(a.source));
    const time = card.querySelector(".card-time");
    time.dateTime = a.published_at;
    // Independent pieces can be weeks old, so show the date rather than "há 23 dias".
    const days = (Date.now() - new Date(a.published_at).getTime()) / 86400000;
    const old = days >= OLD_DAYS;
    time.textContent = old ? dayMonth(a.published_at) : timeAgo(a.published_at);
    time.classList.toggle("dated", old);
    time.title = fullDate(a.published_at);
    const badge = card.querySelector(".badge");
    badge.classList.add(a.label);
    badge.querySelector(".badge-text").textContent = LABELS[a.label] || a.label;
    badge.querySelector(".tip").append(tooltipFor(a));
    badge.setAttribute("aria-label",
      `${LABELS[a.label]}. Palavras: ${a.matched_words.map((m) => m.word).join(", ") || "nenhuma"}`);
    const link = card.querySelector(".card-title a");
    link.href = a.url;
    link.textContent = a.title;
    card.querySelector(".card-summary").textContent = a.summary;
    const tags = card.querySelector(".card-topics");
    const labels = new Map(state.topics.map((t) => [t.slug, t.label]));
    for (const slug of a.topics || []) {
      if (labels.has(slug)) tags.append(el("li", {}, labels.get(slug)));
    }
    feedList.append(card);
  }

  renderComposition(data);
  renderSearchHint(data);

  // The share asked for is always honoured, so the feed can be shorter than
  // the size chosen. Say how many there were, not a percentage that matched.
  pctActual.hidden = !data.shortfall;
  if (data.shortfall) {
    const kind = data.limited_by === "non_positive" ? "neutras ou negativas" : "positivas";
    const available = data.limited_by === "non_positive"
      ? data.available_non_positive : data.available_positive;
    pctActual.textContent = data.count
      ? `Só há ${available} notícias ${kind} nesta seleção — a mostrar ${data.count} de ${data.limit}`
      : `Nenhuma notícia ${kind} nesta seleção`;
    pctActual.title =
      `Para manter a proporção pedida (${Math.round(data.requested_pct)}% positivas), `
      + "a lista fica mais curta em vez de incluir notícias que não pediu.";
  }

  if (!independentOn() && !activeSources().length) {
    showMessage("Nenhum jornal selecionado.", "Escolha pelo menos um jornal acima.");
  } else if (!data.articles.length) {
    if (data.limited_by) {
      const kind = data.limited_by === "non_positive" ? "neutras ou negativas" : "positivas";
      showMessage(`Nenhuma notícia ${kind} nesta seleção.`,
        "Mova o cursor para o outro lado, ou escolha mais jornais.");
    } else if (state.query.trim()) {
      showMessage(`Nenhuma notícia com “${state.query.trim()}”.`,
        "Experimente outra palavra, ou limpe a pesquisa no ×.");
    } else if (state.topic) {
      const label = state.topics.find((t) => t.slug === state.topic)?.label || "este tema";
      showMessage(`Nenhuma notícia sobre ${label} com estes filtros.`,
        "Experimente ativar mais jornais ou clicar outra vez no tema.");
    } else if (state.sources.some((s) => s.enabled && state.hidden.has(s.id))) {
      showMessage("Nenhuma notícia com estes filtros.", "Experimente ativar mais jornais.");
    } else {
      showMessage("Ainda não há notícias.", "A primeira atualização pode demorar alguns segundos…");
    }
  } else {
    showMessage(null);
  }

  setUpdated(data.last_updated);
}

const composition = $("#composition");
const meterImg = $("#meter-img");
const legend = $("#legend");

function renderComposition(data) {
  const counts = { positive: 0, neutral: 0, negative: 0 };
  for (const a of data.articles) counts[a.label] = (counts[a.label] || 0) + 1;
  const total = data.articles.length;
  composition.hidden = !total;
  if (!total) return;

  const pct = (n) => `${(100 * n / total).toFixed(2)}%`;
  $("#seg-pos").style.width = pct(counts.positive);
  $("#seg-neu").style.width = pct(counts.neutral);
  $("#seg-neg").style.width = pct(counts.negative);

  legend.replaceChildren();
  const parts = [["pos", counts.positive, "positivas"],
                 ["neu", counts.neutral, "neutras"],
                 ["neg", counts.negative, "negativas"]];
  for (const [kind, n, word] of parts) {
    const item = el("span");
    item.append(el("span", { className: `key key-${kind}` }), el("b", {}, String(n)), ` ${word}`);
    legend.append(item);
  }
  meterImg.setAttribute("aria-label",
    `${total} notícias: ${parts.map(([, n, w]) => `${n} ${w}`).join(", ")}`);
}

function showMessage(text, hint) {
  if (text) feedList.replaceChildren();
  message.hidden = !text;
  message.replaceChildren();
  if (!text) return;
  message.append(text);
  if (hint) message.append(el("span", { className: "hint" }, hint));
}

let feedRequest = 0;
async function loadFeed({ skeleton = false } = {}) {
  const id = ++feedRequest;
  if (skeleton) showSkeletons();
  const params = new URLSearchParams({ positive_pct: state.pct, limit: state.limit,
                                       scope: state.scope, group: state.group });
  // While "Independentes" is on the outlet toggles are ignored entirely.
  if (state.sources.length && !independentOn()) {
    params.set("sources", activeSources().join(","));
  }
  if (state.topic) params.set("topics", state.topic);
  if (state.query.trim()) params.set("q", state.query.trim());
  try {
    const res = await fetch(`/api/feed?${params}`);
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    if (id === feedRequest) {
      feedList.setAttribute("aria-busy", "false");
      renderFeed(data);                       // ignore out-of-order responses
    }
  } catch (err) {
    if (id === feedRequest) showMessage(`Não foi possível carregar as notícias (${err.message}). O servidor está a correr?`);
  }
}

// ---------- last updated / refresh ----------
const updatedEl = $("#updated");
function setUpdated(iso) {
  state.lastUpdated = iso;
  if (!iso) { updatedEl.textContent = "A atualizar…"; updatedEl.title = ""; return; }
  updatedEl.textContent = `Atualizado ${timeAgo(iso)}`;
  updatedEl.title = fullDate(iso);
}

const refreshBtn = $("#refresh");
async function refresh() {
  refreshBtn.disabled = true;
  refreshBtn.classList.add("spinning");
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    const data = await res.json();
    if (data.skipped) await waitUntilIdle(); // an automatic update was already running
  } catch { /* shown by loadFeed below if the server is down */ }
  await Promise.all([loadSources(), loadTopics()]);
  await loadFeed();
  refreshBtn.disabled = false;
  refreshBtn.classList.remove("spinning");
}
refreshBtn.addEventListener("click", refresh);

async function waitUntilIdle() {
  for (let i = 0; i < 60; i++) {
    const s = await (await fetch("/api/status")).json();
    if (!s.running) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// Pick up automatic (scheduled) updates and keep "há X min" fresh.
async function poll() {
  try {
    const s = await (await fetch("/api/status")).json();
    if (s.last_updated && s.last_updated !== state.lastUpdated && !s.running) {
      await Promise.all([loadSources(), loadTopics()]);
      await loadFeed();
    } else {
      setUpdated(state.lastUpdated);
      document.querySelectorAll(".card-time").forEach((t) => { t.textContent = timeAgo(t.dateTime); });
    }
  } catch { /* server stopped; try again later */ }
}

// ---------- start ----------
// A stored "independent" makes no sense outside Portugal (the outlets are all
// Portuguese); without this the page could load an empty, unexplained feed.
if (state.scope !== "portugal" && independentOn()) setGroup("mainstream");

renderPct();
renderScope();
setQuery(state.query, { render: true });
(async () => {
  showSkeletons();
  try { await Promise.all([loadSources(), loadTopics()]); } catch { /* loadFeed will report */ }
  await loadFeed();
  setInterval(poll, 15_000);
})();
