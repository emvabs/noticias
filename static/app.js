"use strict";

const $ = (sel) => document.querySelector(sel);
const LABELS = { positive: "Positiva", neutral: "Neutra", negative: "Negativa" };
const FRESH_MS = 60_000;   // data newer than this counts as fresh (no refetch)

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
  layout: store.get("layout", "cards"),         // "cards" or "list"
  theme: store.get("theme", "auto"),            // "auto", "light" or "dark"
  hidden: new Set(store.get("hiddenSources", [])),
  group: store.get("group", "mainstream"),   // "independent" while the button is on
  sources: [],
  lastUpdated: null,
  lastData: null,                               // the last /api/feed answer
  view: "noticias",
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

function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

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
  search: ["circle:11,11,7", "M16.5 16.5 21 21"],
  refresh: ["M20 11a8 8 0 1 0-2.3 5.7", "M20 4v7h-7"],
  more: ["circle:5,12,1", "circle:12,12,1", "circle:19,12,1"],
  chevron: ["m6 9 6 6 6-6"],
  check: ["m5 12.5 4.5 4.5L19 7.5"],
  news: ["M5 5h11v14H6a2 2 0 0 1-2-2V6a1 1 0 0 1 1-1Z", "M16 9h3v8a2 2 0 0 1-2 2", "M8 9h5", "M8 13h5", "M8 16h3"],
  list: ["M9 6h11", "M9 12h11", "M9 18h11", "M4 6h.01", "M4 12h.01", "M4 18h.01"],
  bookmark: ["M6 3.5h12v17l-6-4-6 4Z"],
  compass: ["circle:12,12,9", "m15.5 8.5-2 5-5 2 2-5Z"],
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

/** <span data-icon="name"> placeholders in the page become line icons. */
function hydrateIcons(root = document) {
  for (const slot of root.querySelectorAll("[data-icon]")) {
    slot.replaceWith(icon(slot.dataset.icon, Number(slot.dataset.size) || 18));
  }
}
hydrateIcons();

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

// ---------- popovers: one open at a time; a bottom sheet on phones ----------
const popovers = (() => {
  let open = null;                     // { btn, pop }
  const backdrop = el("div", { className: "pop-backdrop", hidden: true });
  document.body.append(backdrop);

  function hide({ focus = false } = {}) {
    if (!open) return;
    const { btn, pop } = open;
    open = null;
    pop.hidden = true;
    backdrop.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (focus) btn.focus();
  }

  function show(btn, pop, { keyboard = false } = {}) {
    hide();
    open = { btn, pop };
    pop.hidden = false;
    backdrop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    // opened from the keyboard: into the box, so the keys carry on from there
    if (keyboard) pop.querySelector("input, button:not(.pop-close), a, [tabindex='0']")?.focus({ preventScroll: true });
  }

  function bind(btn, pop) {
    // detail is 0 for a click made with Enter or Space
    btn.addEventListener("click", (e) => (open?.pop === pop ? hide() : show(btn, pop, { keyboard: e.detail === 0 })));
    pop.querySelector(".pop-close")?.addEventListener("click", () => hide({ focus: true }));
  }

  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) hide({ focus: true }); });
  document.addEventListener("pointerdown", (e) => {
    if (open && !open.pop.contains(e.target) && !open.btn.contains(e.target)) hide();
  });
  return { bind, hide, isOpen: (pop) => open?.pop === pop };
})();

/** A row of buttons acting as one choice ("25 | 50 | 100"). */
function segmented(container, value, onChange) {
  const buttons = [...container.querySelectorAll("button")];
  const render = (v) => buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.value === String(v))));
  render(value);
  for (const b of buttons) {
    b.addEventListener("click", () => { render(b.dataset.value); onChange(b.dataset.value); });
  }
}

// ---------- tone: the positivity slider, in its popover ----------
const slider = $("#pct");
const pctValue = $("#pct-value");
const toneBtn = $("#tone-btn");
const tonePop = $("#tone-pop");
popovers.bind(toneBtn, tonePop);

function renderPct() {
  slider.value = state.pct;
  slider.style.setProperty("--fill", `${state.pct}%`);
  pctValue.querySelector("strong").textContent = `${state.pct}%`;
  $("#tone-value").textContent = `${state.pct}%`;
  toneBtn.setAttribute("aria-label", `Tom do feed: ${state.pct}% de notícias positivas`);
}

function setPct(value) {
  state.pct = Math.max(0, Math.min(100, Math.round(value / 5) * 5));
  store.set("positivePct", state.pct);
  renderPct();
}

let debounce;
slider.addEventListener("input", () => {
  setPct(Number(slider.value));
  clearTimeout(debounce);
  debounce = setTimeout(loadFeed, 150);
});

// ---------- options menu: page size, layout, theme ----------
popovers.bind($("#more-btn"), $("#more-pop"));
for (const a of document.querySelectorAll("#more-pop a")) a.addEventListener("click", () => popovers.hide());

segmented($("#limit"), state.limit, (v) => {
  state.limit = Number(v);
  store.set("limit", state.limit);
  loadFeed();
});

function applyLayout() { document.body.dataset.layout = state.layout; }
segmented($("#layout"), state.layout, (v) => {
  state.layout = v;
  store.set("layout", v);
  applyLayout();
});
applyLayout();

function applyTheme() {
  if (state.theme === "light" || state.theme === "dark") document.documentElement.dataset.theme = state.theme;
  else delete document.documentElement.dataset.theme;
}
segmented($("#theme"), state.theme, (v) => {
  state.theme = v;
  store.set("theme", v);
  applyTheme();
});

// ---------- scope switch (Portugal / Mundo) ----------
const scopeButtons = [...document.querySelectorAll(".scope-btn[data-scope]")];

function renderScope() {
  // "Independentes" is Portugal's independent outlets: a selection of its own
  const current = independentOn() ? "independent" : state.scope;
  for (const btn of scopeButtons) {
    btn.setAttribute("aria-pressed", String(btn.dataset.scope === current));
  }
}

for (const btn of scopeButtons) {
  btn.addEventListener("click", async () => {
    const wanted = btn.dataset.scope;
    const current = independentOn() ? "independent" : state.scope;
    if (wanted === current) return;
    const scope = wanted === "independent" ? "portugal" : wanted;
    const scopeChanged = scope !== state.scope;
    state.scope = scope;                  // the slider value is kept
    store.set("scope", scope);
    setGroup(wanted === "independent" ? "independent" : "mainstream");
    setTopic(null);                       // topics differ per scope and group
    setQuery("", { render: true });
    renderScope();
    showSkeletons();
    await Promise.all([loadSources(), loadTopics()]);   // both belong to one selection
    await loadFeed();                     // show what we already have, right away
    // Then fetch the feeds, unless that just happened (toggling back and forth
    // shouldn't hit every outlet again).
    if (scopeChanged && (!state.lastUpdated
        || Date.now() - new Date(state.lastUpdated).getTime() > FRESH_MS)) {
      await refresh();
    }
  });
}

// ---------- outlets: the "Jornais" menu ----------
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

const outletsBtn = $("#outlets-btn");
const outletsPop = $("#outlets-pop");
const outletsBody = $("#outlets-body");
popovers.bind(outletsBtn, outletsPop);

function setHidden(ids) {
  state.hidden = new Set(ids);
  store.set("hiddenSources", [...state.hidden]);
  renderSources();
  loadFeed();
}

function daysSince(iso) {
  return iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86400000) : null;
}

function renderSources() {
  const enabled = outletChips().filter((s) => s.enabled);
  const shown = enabled.filter((s) => !state.hidden.has(s.id));
  const count = $("#outlets-count");
  count.hidden = independentOn() || shown.length === enabled.length;
  count.textContent = `${shown.length}/${enabled.length}`;
  outletsBtn.classList.toggle("filtered", !count.hidden);
  outletsBtn.classList.toggle("has-error", enabled.some((s) => s.errors.length));

  outletsBody.replaceChildren();
  if (independentOn()) renderIndependentList();
  else renderOutletList(enabled);
}

function renderOutletList(enabled) {
  $("#outlets-title").textContent = "Jornais";
  const list = el("ul", { className: "outlet-list" });
  for (const s of enabled) {
    const li = el("li");
    const label = el("label", { className: "outlet-row" });
    const box = el("input", { type: "checkbox", checked: !state.hidden.has(s.id) });
    box.addEventListener("change", () => {
      const next = new Set(state.hidden);
      box.checked ? next.delete(s.id) : next.add(s.id);
      setHidden(next);
    });
    const dot = el("span", { className: "chip-dot", ariaHidden: "true" });
    dot.style.setProperty("--hue", hueFor(s.id));
    label.append(box, dot, el("span", { className: "outlet-name" }, s.name),
      el("span", { className: "outlet-n" }, String(s.articles)));
    if (s.errors.length) {
      const warn = el("span", { className: "warn", title: `Erro na última atualização:\n${s.errors.join("\n")}` });
      warn.setAttribute("aria-label", "Erro na última atualização");
      label.append(warn);
    }
    // "só este": every other outlet off in one click
    const only = el("button", { type: "button", className: "outlet-only" }, "só este");
    only.setAttribute("aria-label", `Mostrar só ${s.name}`);
    only.addEventListener("click", () => setHidden(enabled.filter((o) => o.id !== s.id).map((o) => o.id)));
    li.append(label, only);
    list.append(li);
  }
  const foot = el("div", { className: "pop-foot" });
  if (state.hidden.size) {
    const all = el("button", { type: "button", className: "link-btn" }, "Mostrar todos");
    all.addEventListener("click", () => setHidden([]));
    foot.append(all);
  }
  const off = outletChips().filter((s) => !s.enabled);
  if (off.length) {
    foot.append(el("p", { className: "pop-note" },
      `Sem feed RSS: ${off.map((s) => s.name).join(", ")}.`));
  }
  outletsBody.append(list, foot);
}

function renderIndependentList() {
  // These outlets publish rarely, so "last piece" is the only way to notice a
  // feed that quietly stopped working.
  $("#outlets-title").textContent = "Independentes";
  const list = el("ul", { className: "outlet-list" });
  for (const s of independentSources()) {
    const age = daysSince(s.last_published);
    const text = age === null ? "sem artigos" : age === 0 ? "hoje" : age === 1 ? "ontem" : `há ${age} dias`;
    const li = el("li", { className: "outlet-row static" });
    const dot = el("span", { className: "chip-dot", ariaHidden: "true" });
    dot.style.setProperty("--hue", hueFor(s.id));
    li.append(dot, el("span", { className: "outlet-name" }, s.name),
      el("span", { className: `outlet-age${age === null || age > 30 ? " stale" : ""}` }, `última peça ${text}`));
    if (s.errors.length) li.title = s.errors.join("\n");
    list.append(li);
  }
  outletsBody.append(list, el("p", { className: "pop-note" },
    "Publicam poucas peças por mês: se uma ficar muito tempo sem nada, o feed pode ter deixado de funcionar."
    + " Ao voltar a Portugal, volta a sua seleção de jornais."));
}

async function loadSources() {
  // Always the whole scope: the menu lists the mainstream outlets and, while
  // "Independentes" is on, the independents with their last piece.
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
const topbar = $(".topbar");

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
  topbar.classList.toggle("has-query", !!value);
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
  loadFeed();
  // on phones the box closes with the query; elsewhere it keeps the focus
  if (topbar.classList.contains("searching")) topbar.classList.remove("searching");
  else searchInput.focus();
});

// Phones: the box takes the whole bar while it is open.
$("#search-open").addEventListener("click", () => {
  topbar.classList.add("searching");
  searchInput.focus();
});
searchInput.addEventListener("blur", () => {
  if (!state.query) topbar.classList.remove("searching");
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
const feedEnd = $("#feed-end");
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
// Opens on click only (the "i" button, the outlet's or a journalist's name):
// a side panel on wide screens, a bottom sheet on phones. Short first, sources on demand.
const whois = (() => {
  const box = $("#whois");
  const body = box.querySelector(".whois-body");
  const backdrop = el("div", { className: "whois-backdrop", hidden: true });
  document.body.append(backdrop);
  const cache = new Map();
  let current = null;        // { article, card, trigger }

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
  function clipText(text, n) { return text.length > n ? `${text.slice(0, n - 1).trimEnd()}…` : text; }
  function itemText(item) { return typeof item === "string" ? item : item?.text || ""; }

  function statsText(stats, { outlets = false } = {}) {
    if (!stats || !stats.articles) return null;
    const { positive, negative } = stats.labels;
    let text = `${plural(stats.articles, "notícia", "notícias")} · ${pct(positive, stats.articles)}% positivas`
      + ` · ${pct(negative, stats.articles)}% negativas`;
    if (outlets && stats.outlets.length) text += ` · em ${stats.outlets.join(", ")}`;
    return text;
  }

  function host(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "fonte"; }
  }

  // One claim with its source link: {text, source} (source may be a list).
  function claim(item) {
    const li = el("li");
    li.append(itemText(item));
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

  /** The short version of the political links: none, or the first one and a count. */
  function politicsLine(p, empty) {
    const all = [...(p?.official || []).map((i) => ["Oficial", i]), ...(p?.reported || []).map((i) => ["Reportado", i])];
    if (!all.length) return empty;
    const [kind, first] = all[0];
    return `${kind}: ${clipText(itemText(first), 110)}${all.length > 1 ? ` (+${all.length - 1})` : ""}`;
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

  function investigateLink(tab, id) {
    const a = el("a", { href: `#investigar/${tab}/${id}`, className: "whois-dd" }, "Investigar");
    a.append(icon("arrow", 14));
    a.addEventListener("click", () => close({ focus: false }));
    return a;
  }

  /** "Dono · Financiamento · Ligações · Nesta app" as label/value rows. */
  function facts(rows) {
    const dl = el("dl", { className: "whois-facts" });
    for (const [label, value] of rows) {
      if (!value) continue;
      dl.append(el("dt", {}, label), el("dd", {}, value));
    }
    return dl;
  }

  /** The full, sourced claims, folded until asked for. */
  function more(build) {
    const det = el("details", { className: "whois-more" });
    det.append(el("summary", {}, "Ver tudo, com as fontes"));
    const inner = el("div");
    build(inner);
    det.append(inner);
    return det;
  }

  function head(sec, kicker, name, link) {
    const top = el("div", { className: "whois-top" });
    const titles = el("div");
    titles.append(el("h3", {}, kicker), el("p", { className: "whois-name" }, name));
    top.append(titles);
    if (link) top.append(link);
    sec.append(top);
  }

  function outletSection(data, kicker) {
    const sec = el("section", { className: "whois-sec" });
    head(sec, kicker, data.name || data.id, data.id ? investigateLink("jornais", data.id) : null);
    const p = data.profile;
    if (p?.type) sec.append(el("p", { className: "whois-type" }, p.type));
    const funding = p?.funding || [];
    sec.append(facts([
      ["Dono", p ? p.owner || "Sem informação" : null],
      ["Financiamento", p ? (funding.length ? clipText(itemText(funding[0]), 90)
        + (funding.length > 1 ? ` (+${funding.length - 1})` : "") : "Sem informação pública") : null],
      ["Ligações políticas", p ? politicsLine(p.political_links, "Sem ligações conhecidas documentadas") : null],
      ["Nesta app", statsText(data.stats, { outlets: !p?.owner && data.stats?.outlets?.length > 1 })],
    ]));
    if (!p) {
      sec.append(el("p", { className: "whois-none" }, "Perfil ainda não investigado."));
    } else {
      sec.append(more((inner) => {
        if (p.summary) inner.append(el("p", {}, p.summary));
        inner.append(list("Financiamento", p.funding, "Sem informação pública."));
        inner.append(politics(p.political_links, "Sem ligações conhecidas documentadas."));
        inner.append(list("Narrativa e críticas", p.narrative, "Sem padrões documentados."));
      }));
    }
    const c = checked(data);
    if (c) sec.append(c);
    return sec;
  }

  function journalistSection(data, name) {
    const sec = el("section", { className: "whois-sec" });
    head(sec, "Jornalista", data?.name || name, data?.id ? investigateLink("jornalistas", data.id) : null);
    const p = data?.profile;
    if (p?.role) sec.append(el("p", { className: "whois-type" }, p.role));
    sec.append(facts([
      ["Ligações políticas", p ? politicsLine(p.political_links, "Sem ligações públicas conhecidas") : null],
      ["Nesta app", statsText(data?.stats, { outlets: true })],
    ]));
    if (!p) {
      sec.append(el("p", { className: "whois-none" }, "Perfil ainda não investigado."));
    } else {
      sec.append(more((inner) => {
        if (p.summary) inner.append(el("p", {}, p.summary));
        if (p.career?.length) inner.append(list("Percurso", p.career));
        inner.append(politics(p.political_links, "Sem ligações públicas conhecidas."));
        inner.append(list("Narrativa e críticas", p.narrative, "Sem padrões documentados."));
      }));
    }
    const c = checked(data);
    if (c) sec.append(c);
    return sec;
  }

  async function fill(article, first) {
    body.replaceChildren(el("p", { className: "whois-loading" }, "A carregar…"));
    const authors = (article.authors || []).filter((a) => !(a.kind === "outlet" && a.id === article.source));
    const [outletData, ...authorData] = await Promise.all([
      profile("outlet", article.source),
      ...authors.slice(0, 3).map((a) => (a.kind === "other" ? null : profile(a.kind, a.id))),
    ]);
    if (current?.article !== article) return;    // another one was opened meanwhile

    const outlet = outletSection(outletData || { id: article.source, name: article.source_name }, "Jornal");
    const people = [];
    if (!authors.length) {
      const sec = el("section", { className: "whois-sec" });
      sec.append(el("h3", {}, "Jornalista"), el("p", { className: "whois-none" }, "A notícia não indica o jornalista."));
      people.push(sec);
    }
    authors.slice(0, 3).forEach((a, i) => {
      let sec;
      if (a.kind === "outlet") sec = outletSection(authorData[i] || { id: a.id, name: a.name }, "Agência / redação");
      else if (a.kind === "other") {
        sec = el("section", { className: "whois-sec" });
        sec.append(el("h3", {}, "Autoria"), el("p", { className: "whois-name" }, a.name));
      } else sec = journalistSection(authorData[i], a.name);
      sec.dataset.who = a.name;
      people.push(sec);
    });
    // the name that was clicked goes first
    const clicked = first ? people.find((s) => s.dataset.who === first) : null;
    const order = clicked ? [clicked, outlet, ...people.filter((s) => s !== clicked)] : [outlet, ...people];
    const frag = document.createDocumentFragment();
    frag.append(el("p", { className: "whois-article" }, article.title), ...order,
      el("p", { className: "whois-note" },
        "Compilado de fontes públicas, citadas em cada linha. Não é uma avaliação da notícia."));
    body.replaceChildren(frag);
    body.scrollTop = 0;
  }

  function open(card, article, trigger, first = null) {
    current?.card.classList.remove("whois-on");
    current?.card.querySelector(".whois-btn").setAttribute("aria-expanded", "false");
    current = { card, article, trigger };
    card.classList.add("whois-on");
    card.querySelector(".whois-btn").setAttribute("aria-expanded", "true");
    box.hidden = false;
    backdrop.hidden = false;
    fill(article, first);
    box.querySelector(".whois-close").focus({ preventScroll: true });
  }

  function close({ focus = true } = {}) {
    if (!current) return;
    const { card, trigger } = current;
    current = null;
    card.classList.remove("whois-on");
    card.querySelector(".whois-btn").setAttribute("aria-expanded", "false");
    box.hidden = true;
    backdrop.hidden = true;
    if (focus && trigger?.isConnected) trigger.focus({ preventScroll: true });
  }

  box.querySelector(".whois-close").addEventListener("click", () => close());
  backdrop.addEventListener("click", () => close());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && current) close(); });
  document.addEventListener("pointerdown", (e) => {
    if (current && !box.contains(e.target) && !e.target.closest("[data-whois]")) close({ focus: false });
  });

  function attach(card, article) {
    const toggle = (trigger, first) => (current?.card === card && !first ? close() : open(card, article, trigger, first));
    for (const trigger of card.querySelectorAll("[data-whois]")) {
      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        toggle(trigger, trigger.dataset.whois === "outlet" ? null : trigger.dataset.whois);
      });
    }
  }

  return { attach, close };
})();
// ---------- new since the last visit, and what was already read (per device) ----------
const seen = {
  since: store.get("lastSeen", null),   // articles fetched after this are new
  shownAt: Date.now(),                  // when the page was last brought into view
  hiddenAt: 0,
  onlyNew: false,
};
const AWAY_MS = 5 * 60_000;            // back after this long: a new visit
const LOOK_MS = 20_000;                // shorter than this (a reload) does not count as a visit

function markSeen() {
  if (Date.now() - seen.shownAt >= LOOK_MS) store.set("lastSeen", new Date().toISOString());
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    markSeen();
    seen.hiddenAt = Date.now();
  } else {
    if (seen.hiddenAt && Date.now() - seen.hiddenAt > AWAY_MS) {
      seen.since = store.get("lastSeen", seen.since);
      seen.onlyNew = false;
      loadFeed();
    }
    seen.shownAt = Date.now();
  }
});
window.addEventListener("pagehide", markSeen);

const isNew = (a) => !!seen.since && a.fetched_at > seen.since;

const read = (() => {
  const KEEP_MS = 15 * 86400000;        // a little longer than the feed keeps articles
  const MAX = 3000;
  const cutoff = Date.now() - KEEP_MS;
  let opened = Object.entries(store.get("readArticles", {}))
    .filter(([, t]) => t > cutoff).sort((x, y) => y[1] - x[1]).slice(0, MAX);
  const map = Object.fromEntries(opened);
  store.set("readArticles", map);
  opened = null;
  return {
    has: (url) => url in map,
    add(url) { map[url] = Date.now(); store.set("readArticles", map); },
  };
})();

// ---------- time groups: "Últimas horas", "Hoje", "Ontem", then dates ----------
const RECENT_HOURS = 3;

function groupLabel(iso, now = new Date()) {
  const d = new Date(iso);
  if ((now - d) / 3600000 < RECENT_HOURS) return "Últimas horas";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return "Hoje";
  if (d.toDateString() === yesterday.toDateString()) return "Ontem";
  return d.toLocaleDateString("pt-PT", { weekday: "long", day: "numeric", month: "long" });
}

function clock(iso) {
  return new Date(iso).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
}

function groupHead(label) {
  const li = el("li", { className: "group-head" });
  li.append(el("h2", {}, label));
  return li;
}

function buildCard(a, group) {
  const card = tpl.content.firstElementChild.cloneNode(true);
  card.classList.add(a.label);
  card.dataset.id = a.id;
  card.querySelector(".source-name").textContent = a.source_name;
  card.querySelector(".source-dot").textContent = initials(a.source_name);
  card.style.setProperty("--hue", hueFor(a.source));
  const time = card.querySelector(".card-time");
  time.dateTime = a.published_at;
  // the group heading carries the day: recent pieces say "há 40 min", the rest the hour
  const recent = group === "Últimas horas";
  time.textContent = recent ? timeAgo(a.published_at) : clock(a.published_at);
  time.classList.toggle("rel", recent);
  time.title = fullDate(a.published_at);

  const tone = card.querySelector(".tone-dot");
  tone.classList.add(a.label);
  tone.querySelector(".tip").append(tooltipFor(a));
  tone.setAttribute("aria-label", `Tom: ${(LABELS[a.label] || a.label).toLowerCase()}. `
    + `Palavras: ${a.matched_words.map((m) => m.word).join(", ") || "nenhuma"}`);

  const link = card.querySelector(".card-title a");
  link.href = a.url;
  link.textContent = a.title;
  const markRead = () => { read.add(a.url); card.classList.add("is-read"); };
  link.addEventListener("click", markRead);
  link.addEventListener("auxclick", (e) => { if (e.button === 1) markRead(); });
  card.classList.toggle("is-read", read.has(a.url));
  if (isNew(a)) {
    card.classList.add("is-new");
    card.querySelector(".new-tag").hidden = false;
  }

  card.querySelector(".card-summary").textContent = a.summary;
  // the outlet's and the journalists' names open "who is behind this"
  const byline = card.querySelector(".card-byline");
  const people = (a.authors || []).filter((p) => !(p.kind === "outlet" && p.id === a.source));
  if (people.length) {
    byline.append("por ");
    people.forEach((p, i) => {
      if (i) byline.append(", ");
      const b = el("button", { type: "button", className: "name-btn" }, p.name);
      b.dataset.whois = p.name;
      b.setAttribute("aria-haspopup", "dialog");
      byline.append(b);
    });
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
  return card;
}

/** The cards, under their time headings; only the new ones if asked. */
function renderList(articles) {
  whois.close();              // its card is about to be replaced
  feedList.replaceChildren();
  const now = new Date();
  let last = null;
  for (const a of seen.onlyNew ? articles.filter(isNew) : articles) {
    const group = groupLabel(a.published_at, now);
    if (group !== last) feedList.append(groupHead(group));
    last = group;
    feedList.append(buildCard(a, group));
  }
}

const newsBar = $("#news-bar");
const newsOnly = $("#news-only");

function renderNewsBar(articles) {
  const n = articles.filter(isNew).length;
  if (!n) seen.onlyNew = false;
  newsBar.hidden = !n;
  $("#news-count").textContent = `${plural(n, "nova", "novas")} desde a sua última visita`;
  newsOnly.textContent = seen.onlyNew ? "Ver todas" : "Ver só as novas";
  newsOnly.setAttribute("aria-pressed", String(seen.onlyNew));
}

newsOnly.addEventListener("click", () => {
  seen.onlyNew = !seen.onlyNew;
  if (state.lastData) { renderList(state.lastData.articles); renderNewsBar(state.lastData.articles); }
});

function renderFeed(data) {
  state.lastData = data;
  renderList(data.articles);
  renderNewsBar(data.articles);

  renderComposition(data);
  renderSearchHint(data);
  renderShortfall(data);

  if (!independentOn() && !activeSources().length) {
    showMessage("Nenhum jornal selecionado.", "Escolha pelo menos um jornal no menu “Jornais”.");
  } else if (!data.articles.length) {
    if (data.limited_by) {
      const kind = data.limited_by === "non_positive" ? "neutras ou negativas" : "positivas";
      showMessage(`Nenhuma notícia ${kind} nesta seleção.`,
        "Ajuste o tom, ou escolha mais jornais.", toneFix(data));
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

/**
 * The nearest share that gives a full page, or null. Short of positives:
 * round down to what they can fill; short of the rest: round up.
 */
function suggestedPct(data) {
  if (!data.limit) return null;
  const step = (x) => x / 5;
  let pct = null;
  if (data.limited_by === "positive") {
    pct = 5 * Math.floor(step((100 * data.available_positive) / data.limit));
  } else if (data.limited_by === "non_positive") {
    pct = 5 * Math.ceil(step((100 * (data.limit - data.available_non_positive)) / data.limit));
  }
  return pct === null || pct === state.pct || pct < 0 || pct > 100 ? null : pct;
}

/** A button that moves the slider to the suggested share, or null. */
function toneFix(data) {
  const pct = suggestedPct(data);
  if (pct === null) return null;
  const btn = el("button", { type: "button", className: "link-btn" }, `Ajustar o tom para ${pct}%`);
  btn.addEventListener("click", () => { setPct(pct); loadFeed(); });
  return btn;
}

// The share asked for is always honoured, so the feed can be shorter than the
// size chosen. Said calmly at the end of the list, not as an alarm at the top.
function renderShortfall(data) {
  toneBtn.querySelector(".tone-alert").hidden = !data.shortfall;
  feedEnd.hidden = !(data.shortfall && data.count);
  feedEnd.replaceChildren();
  if (feedEnd.hidden) return;
  const kind = data.limited_by === "non_positive" ? "neutras ou negativas" : "positivas";
  const available = data.limited_by === "non_positive"
    ? data.available_non_positive : data.available_positive;
  feedEnd.append(`Só há ${available} notícias ${kind} nesta seleção, por isso a lista tem `
    + `${data.count} em vez de ${data.limit}: o tom de ${Math.round(data.requested_pct)}% é respeitado. `);
  const fix = toneFix(data);
  if (fix) feedEnd.append(fix);
}

const composition = $("#composition");
const meterImg = $("#meter-img");
const legend = $("#legend");

function renderComposition(data) {
  const counts = { positive: 0, neutral: 0, negative: 0 };
  for (const a of data.articles) counts[a.label] = (counts[a.label] || 0) + 1;
  const total = data.articles.length;
  composition.hidden = !total;
  const pct = (n) => `${total ? (100 * n / total).toFixed(2) : 0}%`;
  // the small bar in the button shows the same thing as the meter in the box
  const mini = toneBtn.querySelector(".tone-mini");
  mini.children[0].style.width = pct(counts.positive);
  mini.children[1].style.width = pct(counts.neutral);
  mini.children[2].style.width = pct(counts.negative);
  if (!total) return;

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
  const summary = `Nesta lista: ${parts.map(([, n, w]) => `${n} ${w}`).join(", ")}`;
  meterImg.setAttribute("aria-label", summary);
  toneBtn.title = summary + (data.shortfall ? ` (${data.count} de ${data.limit})` : "");
}

function showMessage(text, hint, action) {
  if (text) feedList.replaceChildren();
  message.hidden = !text;
  message.replaceChildren();
  if (!text) return;
  message.append(text);
  if (hint) message.append(el("span", { className: "hint" }, hint));
  if (action) message.append(el("span", { className: "action" }, ""), action);
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
const refreshBtn = $("#refresh");
function setUpdated(iso) {
  state.lastUpdated = iso;
  const text = iso ? `Atualizado ${timeAgo(iso)}` : "A atualizar…";
  updatedEl.textContent = text;
  updatedEl.title = iso ? fullDate(iso) : "";
  refreshBtn.title = `Atualizar agora · ${text.toLowerCase()}`;
}

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
      document.querySelectorAll(".card-time.rel").forEach((t) => { t.textContent = timeAgo(t.dateTime); });
    }
  } catch { /* server stopped; try again later */ }
}

// ---------- research to do: journalists without a profile, old profiles ----------
const profilesStatus = $("#profiles-status");


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

// ---------- views: #noticias, #resumo, #guardados, #silenciados, #investigar/… ----------
const VIEWS = ["noticias", "resumo", "guardados", "silenciados", "investigar"];
const VIEW_ELEMENT = { investigar: "dd" };

function parseRoute() {
  let hash = decodeURIComponent(location.hash.slice(1));
  // links from before the rename keep working
  if (hash === "deep-dive" || hash.startsWith("deep-dive/")) {
    hash = hash.replace(/^deep-dive/, "investigar");
    history.replaceState(null, "", `#${hash}`);
  }
  const parts = hash.split("/");
  return { view: VIEWS.includes(parts[0]) ? parts[0] : "noticias", rest: parts.slice(1) };
}

function route() {
  const { view, rest } = parseRoute();
  const changed = view !== state.view;
  state.view = view;
  document.body.dataset.view = view;
  popovers.hide();
  whois.close();
  for (const name of VIEWS) {
    const node = document.getElementById(VIEW_ELEMENT[name] || `view-${name}`);
    if (node) node.hidden = name !== view;
  }
  for (const a of document.querySelectorAll(".viewnav a")) {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (view === "investigar") window.ddRoute?.(rest);
  if (changed) window.scrollTo({ top: 0 });
}
window.addEventListener("hashchange", route);

// the skip link moves the focus without touching the address
$(".skip").addEventListener("click", (e) => {
  e.preventDefault();
  feedList.setAttribute("tabindex", "-1");
  feedList.focus();
});

// ---------- start ----------
// A stored "independent" makes no sense outside Portugal (the outlets are all
// Portuguese); without this the page could load an empty, unexplained feed.
if (state.scope !== "portugal" && independentOn()) setGroup("mainstream");

renderPct();
renderScope();
setQuery(state.query, { render: true });
// deepdive.js loads after this file: route once both are in
document.addEventListener("DOMContentLoaded", route);
(async () => {
  showSkeletons();
  try { await Promise.all([loadSources(), loadTopics()]); } catch { /* loadFeed will report */ }
  await loadFeed();
  loadProfilesStatus();
  setInterval(poll, 15_000);
})();
