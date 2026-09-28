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

// ---------- line icons (24×24, stroked with the text colour) ----------
const ICONS = {
  info: ["M12 11v5", "M12 7.5h.01", "circle:12,12,9"],
  alert: ["M12 3.5 2.8 19.5h18.4Z", "M12 10v4", "M12 17h.01"],
  doc: ["M7 3h7l4 4v14H7Z", "M14 3v4h4", "M9.5 12h5", "M9.5 16h5"],
  gauge: ["M4 17a8 8 0 0 1 16 0", "M12 17l4-5", "M4 20h16"],
  hash: ["M5 9h14", "M5 15h14", "M10 4 8 20", "M16 4l-2 16"],
  wire: ["circle:12,12,2", "M8.5 8.5a5 5 0 0 0 0 7", "M15.5 8.5a5 5 0 0 1 0 7",
         "M5.6 5.6a9 9 0 0 0 0 12.8", "M18.4 5.6a9 9 0 0 1 0 12.8"],
  building: ["M4 21V8l8-5 8 5v13", "M9 21v-6h6v6", "M3 21h18"],
  mute: ["M11 5 6 9H3v6h3l5 4Z", "M16 9l5 6", "M21 9l-5 6"],
  x: ["M6 6l12 12", "M18 6 6 18"],
  arrow: ["M5 12h14", "M13 6l6 6-6 6"],
};

/** An inline SVG icon; decorative unless the caller labels it. */
function icon(name, size = 16) {
  const NS = "http://www.w3.org/2000/svg";
  const node = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, fill: "none",
    stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round",
    class: "icon", "aria-hidden": "true" })) node.setAttribute(k, v);
  for (const d of ICONS[name] || []) {
    if (d.startsWith("circle:")) {
      const [cx, cy, r] = d.slice(7).split(",");
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", cx); c.setAttribute("cy", cy); c.setAttribute("r", r);
      node.append(c);
    } else {
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      node.append(path);
    }
  }
  return node;
}

/** "CNN Portugal" -> "CNN", "Diário de Notícias" -> "DN", "The Guardian" -> "G". */
function initials(name) {
  const words = (name || "").split(/\s+/).filter((w) => !/^(the|de|da|do|das|dos|para|e)$/i.test(w));
  if (/^[A-Z]{2,4}$/.test(words[0] || "")) return words[0];
  return words.slice(0, 2).map((w) => w[0]).join("").toUpperCase();
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
    const chip = el("button", { type: "button", className: "chip" });
    const dot = el("span", { className: "chip-dot", ariaHidden: "true" });
    dot.style.setProperty("--hue", hueFor(s.id));
    chip.append(dot, s.name);
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

// ---------- "who is behind this": outlet and journalist profiles ----------
// Hover a card (mouse) for a moment, or press its "i" button (touch, keyboard).
const whois = (() => {
  const box = $("#whois");
  const body = box.querySelector(".whois-body");
  const OPEN_DELAY = 400;    // ms: scrolling past cards must not flash the box
  const CLOSE_DELAY = 250;   // ms: time to move the pointer from the card onto the box
  const cache = new Map();
  let openTimer = 0;
  let closeTimer = 0;
  let current = null;        // the card the box belongs to

  function profile(kind, id) {
    const key = `${kind}:${id}`;
    if (!cache.has(key)) {
      cache.set(key, fetch(`/api/profile/${kind}/${encodeURIComponent(id)}`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => { cache.delete(key); return null; }));
    }
    return cache.get(key);
  }

  function pct(n, total) { return total ? Math.round((100 * n) / total) : 0; }

  function statsLine(stats, { outlets = false } = {}) {
    if (!stats || !stats.articles) return null;
    const { positive, negative } = stats.labels;
    let text = `Nesta app: ${stats.articles} ${stats.articles === 1 ? "notícia" : "notícias"}`
      + ` · ${pct(positive, stats.articles)}% positivas · ${pct(negative, stats.articles)}% negativas`;
    if (outlets && stats.outlets.length) text += ` · em ${stats.outlets.join(", ")}`;
    return el("p", { className: "whois-stats" }, text);
  }

  function host(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "fonte"; }
  }

  // One claim with its source link: {text, source} (source may be a list).
  function claim(item) {
    const li = el("li");
    const text = typeof item === "string" ? item : item.text;
    li.append(text || "");
    const sources = typeof item === "string" ? [] : [].concat(item.source || []);
    for (const url of sources) {
      li.append(" ", el("a", { href: url, target: "_blank", rel: "noopener noreferrer",
        className: "whois-src", title: url }, host(url)));
    }
    return li;
  }

  function list(title, items, empty) {
    const frag = document.createDocumentFragment();
    if (!items?.length && !empty) return frag;
    frag.append(el("h4", {}, title));
    if (!items?.length) {
      frag.append(el("p", { className: "whois-none" }, empty));
      return frag;
    }
    const ul = el("ul");
    items.forEach((i) => ul.append(claim(i)));
    frag.append(ul);
    return frag;
  }

  function politics(p, empty) {
    const official = p?.official || [];
    const reported = p?.reported || [];
    const frag = document.createDocumentFragment();
    frag.append(el("h4", {}, "Ligações políticas"));
    if (!official.length && !reported.length) {
      frag.append(el("p", { className: "whois-none" }, empty));
      return frag;
    }
    const ul = el("ul");
    official.forEach((i) => { const li = claim(i); li.prepend(el("b", {}, "Oficial: ")); ul.append(li); });
    reported.forEach((i) => { const li = claim(i); li.prepend(el("b", {}, "Reportado: ")); ul.append(li); });
    frag.append(ul);
    return frag;
  }

  function checked(data) {
    const p = data?.profile;
    if (!p?.last_checked) return null;
    const when = new Date(`${p.last_checked}T12:00`).toLocaleDateString("pt-PT",
      { day: "numeric", month: "long", year: "numeric" });
    // An old profile is still shown, but says it may be out of date.
    return el("p", { className: `whois-checked${data.stale ? " stale" : ""}` },
      data.stale ? `Verificado em ${when} — pode estar desatualizado` : `Verificado em ${when}`);
  }

  function deepDiveLink(tab, id) {
    const a = el("a", { href: `#deep-dive/${tab}/${id}`, className: "whois-dd" }, "Ver deep dive →");
    a.addEventListener("click", () => close());
    return a;
  }

  function outletSection(data, heading) {
    const sec = el("section", { className: "whois-sec" });
    sec.append(el("h3", {}, heading), el("p", { className: "whois-name" }, data.name || data.id));
    if (data.id) sec.append(deepDiveLink("jornais", data.id));
    const p = data.profile;
    if (!p) {
      sec.append(el("p", { className: "whois-none" }, "Perfil ainda não investigado."));
    } else {
      if (p.type) sec.append(el("p", { className: "whois-type" }, p.type));
      if (p.owner) {
        const owner = el("p", {}, "Proprietário: ");
        owner.append(el("strong", {}, p.owner));
        sec.append(owner);
      }
      if (p.summary) sec.append(el("p", {}, p.summary));
      sec.append(list("Financiamento", p.funding, "Sem informação pública."));
      sec.append(politics(p.political_links, "Sem ligações conhecidas documentadas."));
      sec.append(list("Narrativa e críticas", p.narrative, "Sem padrões documentados."));
    }
    const line = statsLine(data.stats, { outlets: !data.profile?.owner && data.stats?.outlets?.length > 1 });
    if (line) sec.append(line);
    const c = checked(data);
    if (c) sec.append(c);
    return sec;
  }

  function journalistSection(data, name) {
    const sec = el("section", { className: "whois-sec" });
    sec.append(el("p", { className: "whois-name" }, data?.name || name));
    if (data?.id) sec.append(deepDiveLink("jornalistas", data.id));
    const p = data?.profile;
    if (!p) {
      sec.append(el("p", { className: "whois-none" }, "Perfil ainda não investigado."));
    } else {
      if (p.role) sec.append(el("p", { className: "whois-type" }, p.role));
      if (p.summary) sec.append(el("p", {}, p.summary));
      if (p.career?.length) sec.append(list("Percurso", p.career));
      sec.append(politics(p.political_links, "Sem ligações públicas conhecidas."));
      sec.append(list("Narrativa e críticas", p.narrative, "Sem padrões documentados."));
    }
    const line = statsLine(data?.stats, { outlets: true });
    if (line) sec.append(line);
    const c = checked(data);
    if (c) sec.append(c);
    return sec;
  }

  async function fill(article) {
    body.replaceChildren(el("p", { className: "whois-loading" }, "A carregar…"));
    const authors = (article.authors || []).filter((a) => !(a.kind === "outlet" && a.id === article.source));
    const [outletData, ...authorData] = await Promise.all([
      profile("outlet", article.source),
      ...authors.slice(0, 3).map((a) => (a.kind === "other" ? null : profile(a.kind, a.id))),
    ]);
    if (current?.article !== article) return;    // the pointer moved on meanwhile

    const frag = document.createDocumentFragment();
    frag.append(outletSection(outletData || { id: article.source, name: article.source_name }, "Jornal"));

    // A heading for the people; an agency byline brings its own "Agência" heading.
    const persons = authors.filter((a) => a.kind !== "outlet");
    const people = el("section", { className: "whois-sec" });
    if (persons.length || !authors.length) {
      people.append(el("h3", {}, persons.length > 1 ? "Autoria" : "Jornalista"));
      frag.append(people);
    }
    if (!authors.length) {
      people.append(el("p", { className: "whois-none" }, "A notícia não indica o jornalista."));
    }
    authors.slice(0, 3).forEach((a, i) => {
      if (a.kind === "outlet") {
        frag.append(outletSection(authorData[i] || { id: a.id, name: a.name }, "Agência / redação"));
      } else if (a.kind === "other") {
        people.append(el("p", { className: "whois-name" }, a.name));
      } else {
        const sec = journalistSection(authorData[i], a.name);
        sec.classList.add("whois-sub");
        frag.append(sec);
      }
    });
    frag.append(el("p", { className: "whois-note" },
      "Compilado de fontes públicas, citadas em cada linha. Não é uma avaliação da notícia."));
    body.replaceChildren(frag);
    place();
  }

  function place() {
    if (!current) return;
    const narrow = window.innerWidth < 720;
    box.classList.toggle("sheet", narrow);
    if (narrow) { box.style.left = box.style.top = ""; return; }
    const r = current.card.getBoundingClientRect();
    const w = box.offsetWidth;
    const h = box.offsetHeight;
    const gap = 12;
    const fit = (y) => Math.max(gap, Math.min(y, window.innerHeight - h - gap));
    let left;
    let top;
    if (window.innerWidth - r.right >= w + gap * 2) {          // beside the card, right
      left = r.right + gap;
      top = fit(r.top);
    } else if (r.left >= w + gap * 2) {                        // beside the card, left
      left = r.left - w - gap;
      top = fit(r.top);
    } else {                                                   // no room beside: below or above it
      left = Math.max(gap, Math.min(r.left, window.innerWidth - w - gap));
      if (window.innerHeight - r.bottom >= h + gap) top = r.bottom + gap / 2;
      else if (r.top >= h + gap) top = r.top - h - gap / 2;
      else top = fit(r.bottom + gap / 2);
    }
    box.style.left = `${left + window.scrollX}px`;
    box.style.top = `${top + window.scrollY}px`;
  }

  function open(card, article) {
    clearTimeout(closeTimer);
    if (current?.card === card && !box.hidden) return;
    current?.button.setAttribute("aria-expanded", "false");
    current = { card, article, button: card.querySelector(".whois-btn") };
    current.button.setAttribute("aria-expanded", "true");
    box.hidden = false;
    fill(article);
    place();
  }

  function close() {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    if (!current) return;
    current.button.setAttribute("aria-expanded", "false");
    current = null;
    box.hidden = true;
  }

  const later = (fn, ms) => { clearTimeout(closeTimer); closeTimer = setTimeout(fn, ms); };

  box.addEventListener("pointerenter", () => clearTimeout(closeTimer));
  box.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") later(close, CLOSE_DELAY); });
  box.querySelector(".whois-close").addEventListener("click", () => {
    const button = current?.button;
    close();
    button?.focus();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && current) { const b = current.button; close(); b.focus(); }
  });
  document.addEventListener("pointerdown", (e) => {
    if (current && !box.contains(e.target) && !current.card.contains(e.target)) close();
  });
  window.addEventListener("resize", place);

  function attach(card, article) {
    card.addEventListener("pointerenter", (e) => {
      if (e.pointerType !== "mouse") return;
      clearTimeout(openTimer);
      clearTimeout(closeTimer);
      openTimer = setTimeout(() => open(card, article), current ? 0 : OPEN_DELAY);
    });
    card.addEventListener("pointerleave", (e) => {
      if (e.pointerType !== "mouse") return;
      clearTimeout(openTimer);
      if (current?.card === card) later(close, CLOSE_DELAY);
    });
    card.querySelector(".whois-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      if (current?.card === card && !box.hidden) close();
      else { open(card, article); box.querySelector(".whois-close").focus({ preventScroll: true }); }
    });
  }

  return { attach, close };
})();

function renderFeed(data) {
  whois.close();              // its card is about to be replaced
  feedList.replaceChildren();
  for (const a of data.articles) {
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.classList.add(a.label);
    card.querySelector(".source-name").textContent = a.source_name;
    card.querySelector(".source-dot").textContent = initials(a.source_name);
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
    const byline = card.querySelector(".card-byline");
    const people = (a.authors || []).filter((p) => !(p.kind === "outlet" && p.id === a.source));
    if (people.length) {
      byline.textContent = "por " + people.map((p) => p.name).join(", ");
      byline.hidden = false;
    }
    const flag = card.querySelector(".owner-flag");
    if (a.owner_mentions?.length) {
      // The piece names the outlet's own owner, group or a sister company.
      const owners = state.sources.find((s) => s.id === a.source)?.owners || [];
      const named = a.owner_mentions.join(", ");
      // No article before the outlet name: "do Público" but "da CNN Portugal".
      const text = `Esta notícia menciona ${named}, ligado a quem detém ${a.source_name}`
        + (owners.length ? ` (${owners.join("; ")}).` : ".");
      flag.querySelector(".tip").textContent = `${text} Leia sabendo desta relação.`;
      flag.setAttribute("aria-label", `Possível conflito de interesses: ${text}`);
      flag.hidden = false;
    }
    whois.attach(card, a);
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
  loadProfilesStatus();
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
      loadProfilesStatus();
    } else {
      setUpdated(state.lastUpdated);
      document.querySelectorAll(".card-time").forEach((t) => { t.textContent = timeAgo(t.dateTime); });
    }
  } catch { /* server stopped; try again later */ }
}

// ---------- research to do: journalists without a profile, old profiles ----------
const profilesStatus = $("#profiles-status");

function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

async function loadProfilesStatus() {
  let data;
  try { data = await (await fetch("/api/profiles/status")).json(); } catch { return; }
  const { pending, stale } = data;
  profilesStatus.hidden = !pending.length && !stale.length;
  if (profilesStatus.hidden) return;

  const parts = [];
  if (pending.length) parts.push(plural(pending.length, "jornalista sem perfil", "jornalistas sem perfil"));
  if (stale.length) parts.push(plural(stale.length, "perfil a rever", "perfis a rever"));
  profilesStatus.querySelector("summary").textContent = parts.join(" · ");

  const body = profilesStatus.querySelector(".ps-body");
  body.replaceChildren();
  if (pending.length) {
    body.append(el("h3", {}, `Sem perfil, com ${data.min_articles} ou mais notícias`));
    const ul = el("ul");
    for (const p of pending) {
      const li = el("li");
      li.append(el("strong", {}, p.name), ` — ${plural(p.articles, "notícia", "notícias")}`
        + ` (${p.outlets.join(", ")})`);
      if (p.suspect) {
        li.append(" ", el("span", { className: "ps-suspect" },
          `${p.suspect}: talvez não seja uma pessoa (aliases.txt)`));
      }
      ul.append(li);
    }
    body.append(ul);
  }
  if (stale.length) {
    body.append(el("h3", {}, `Verificados há mais de ${data.stale_after_days} dias`));
    const ul = el("ul");
    for (const p of stale) {
      const li = el("li");
      li.append(el("strong", {}, p.name), ` — ${p.last_checked || "sem data"} `,
        el("code", {}, `profiles/${p.kind === "outlet" ? "outlets" : "journalists"}/${p.id}.yaml`));
      ul.append(li);
    }
    body.append(ul);
  }
  const how = el("p", { className: "ps-how" }, "Para ver esta lista no terminal: ");
  how.append(el("code", {}, ".venv/bin/python -m app.profiles"), " — como investigar está no README.");
  body.append(how);
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
  loadProfilesStatus();
  setInterval(poll, 15_000);
})();
