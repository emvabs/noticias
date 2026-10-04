"use strict";
// Deep Dive: ownership map, findings per outlet / journalist, silences.
// Uses the helpers of app.js ($, el, fullDate, dayMonth, LABELS); no libraries —
// the two graphs have a natural fixed shape (radial, two columns), drawn in SVG.

const SVG_NS = "http://www.w3.org/2000/svg";
const DD_TABS = ["inicio", "propriedade", "jornais", "jornalistas", "silencios"];
const KIND_OF_TAB = { jornais: "outlet", jornalistas: "journalist" };
const TAB_OF_KIND = { outlet: "jornais", journalist: "jornalistas" };
const OWNER_KIND_LABELS = {
  state: "Estado", public: "Entidade pública", company: "Empresa", person: "Pessoa / família",
  fund: "Fundo", cooperative: "Cooperativa", nonprofit: "Sem fins lucrativos", trust: "Fundação / trust",
};
// The backend sends an emoji per finding; the page draws a line icon per kind instead.
const FINDING_ICON = {
  researched: "doc", tone: "gauge", topic: "hash", agency: "wire", owner: "building", silence: "mute",
};
const FINDING_CLASS = {
  researched: "f-researched", tone: "f-tone", topic: "f-topic", agency: "f-agency",
  owner: "f-owner", silence: "f-silence",
};

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "fonte"; }
}

function clip(text, n) { return text.length > n ? `${text.slice(0, n - 1)}…` : text; }

// ---------- pan and zoom for an <svg> with a viewBox ----------
function panZoom(svgEl, box) {
  let view = { ...box };
  const apply = () => svgEl.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  apply();
  const toSvg = (dx, dy) => {
    const r = svgEl.getBoundingClientRect();
    return [dx * view.w / r.width, dy * view.h / r.height];
  };
  svgEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = svgEl.getBoundingClientRect();
    const k = e.deltaY > 0 ? 1.12 : 1 / 1.12;
    const px = view.x + (e.clientX - r.left) / r.width * view.w;
    const py = view.y + (e.clientY - r.top) / r.height * view.h;
    const w = Math.min(box.w * 3, Math.max(box.w / 5, view.w * k));
    const h = w * box.h / box.w;
    view = { x: px - (px - view.x) * w / view.w, y: py - (py - view.y) * h / view.h, w, h };
    apply();
  }, { passive: false });
  let drag = null;
  svgEl.addEventListener("pointerdown", (e) => {
    if (e.target.closest("[data-node]")) return;       // nodes keep their own clicks
    drag = { x: e.clientX, y: e.clientY, view: { ...view } };
    svgEl.setPointerCapture(e.pointerId);
    svgEl.classList.add("dragging");
  });
  svgEl.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const [dx, dy] = toSvg(e.clientX - drag.x, e.clientY - drag.y);
    view = { ...drag.view, x: drag.view.x - dx, y: drag.view.y - dy };
    apply();
  });
  const stop = () => { drag = null; svgEl.classList.remove("dragging"); };
  svgEl.addEventListener("pointerup", stop);
  svgEl.addEventListener("pointercancel", stop);
  return {
    zoom(k) {
      const w = Math.min(box.w * 3, Math.max(box.w / 5, view.w * k));
      const h = w * box.h / box.w;
      view = { x: view.x + (view.w - w) / 2, y: view.y + (view.h - h) / 2, w, h };
      apply();
    },
    reset() { view = { ...box }; apply(); },
  };
}

function zoomButtons(container, pz) {
  const bar = el("div", { className: "dd-zoom" });
  for (const [label, text, fn] of [["Aproximar", "+", () => pz.zoom(1 / 1.3)],
                                   ["Afastar", "−", () => pz.zoom(1.3)],
                                   ["Repor vista", "⟲", () => pz.reset()]]) {
    const b = el("button", { type: "button", title: label }, text);
    b.setAttribute("aria-label", label);
    b.addEventListener("click", fn);
    bar.append(b);
  }
  container.append(bar);
}

// ---------- a floating label for graph nodes ----------
const ddTip = el("div", { className: "dd-tip", hidden: true });
document.body.append(ddTip);
function showTip(e, lines) {
  ddTip.replaceChildren(...lines.map((l, i) => el(i ? "div" : "strong", {}, l)));
  ddTip.hidden = false;
  const x = Math.min(e.clientX + 14, window.innerWidth - ddTip.offsetWidth - 8);
  const y = Math.min(e.clientY + 14, window.innerHeight - ddTip.offsetHeight - 8);
  ddTip.style.left = `${x + window.scrollX}px`;
  ddTip.style.top = `${y + window.scrollY}px`;
}
function hideTip() { ddTip.hidden = true; }

// ==========================================================================
// Routing: #investigar[/tab[/id]] (the page router calls ddRoute)
// ==========================================================================
const ddState = { tab: null, id: null, index: {}, silenceScope: "portugal" };

/** Called by the page router with the parts after #investigar: [tab, id]. */
async function ddRoute(parts) {
  const tab = DD_TABS.includes(parts[0]) ? parts[0] : "inicio";
  const id = parts[1] || null;
  for (const a of document.querySelectorAll(".dd-tab")) {
    const selected = a.dataset.tab === tab;
    a.setAttribute("aria-selected", String(selected));
    a.classList.toggle("active", selected);
  }
  $("#dd-inicio").hidden = tab !== "inicio";
  $("#dd-propriedade").hidden = tab !== "propriedade";
  $("#dd-entidade").hidden = !KIND_OF_TAB[tab];
  $("#dd-silencios").hidden = tab !== "silencios";
  const changedTab = tab !== ddState.tab;
  ddState.tab = tab;
  ddState.id = id;
  if (tab === "propriedade" && changedTab) await showOwnership();
  if (KIND_OF_TAB[tab]) await showEntityTab(KIND_OF_TAB[tab], id, changedTab);
  if (tab === "silencios" && changedTab) await showSilences();
  if (changedTab) window.scrollTo({ top: 0 });
}
window.ddRoute = ddRoute;

// ==========================================================================
// Ownership map: owners (left) -> outlets (right)
// ==========================================================================
let ownershipData = null;

async function showOwnership() {
  if (!ownershipData) ownershipData = await (await fetch("/api/ownership")).json();
  drawOwnership(ownershipData);
}

function drawOwnership(data) {
  const svgEl = $("#own-svg");
  svgEl.replaceChildren();
  const graphBox = svgEl.parentElement;
  graphBox.querySelector(".dd-zoom")?.remove();

  const kinds = Object.keys(OWNER_KIND_LABELS);
  const owners = [...data.owners].sort((a, b) =>
    kinds.indexOf(a.kind) - kinds.indexOf(b.kind) || a.name.localeCompare(b.name, "pt"));
  // Outlets ordered by their first owner, so the lines cross as little as possible.
  const firstOwner = (id) => Math.min(...data.links.filter((l) => l.outlet === id)
    .map((l) => owners.findIndex((o) => o.id === l.owner)));
  const outlets = [...data.outlets].sort((a, b) => firstOwner(a.id) - firstOwner(b.id));

  const ROW = 34;
  const W = 760;
  const H = Math.max(owners.length, outlets.length) * ROW + 30;
  const X_OWNER = 20;
  const X_OUTLET = W - 250;
  const yOwner = (i) => 25 + i * (H - 30) / owners.length;
  const yOutlet = (i) => 25 + i * (H - 30) / outlets.length;
  const pos = {};
  owners.forEach((o, i) => { pos[`o:${o.id}`] = yOwner(i); });
  outlets.forEach((o, i) => { pos[`t:${o.id}`] = yOutlet(i); });

  const gLinks = svg("g", { class: "own-links" });
  // Several owners of one outlet: spread their line ends and stack their shares.
  const perOutlet = {};
  for (const l of data.links) (perOutlet[l.outlet] ||= []).push(l);
  for (const l of data.links) {
    const siblings = perOutlet[l.outlet];
    const k = siblings.indexOf(l) - (siblings.length - 1) / 2;
    const y1 = pos[`o:${l.owner}`];
    const y2 = pos[`t:${l.outlet}`] + k * 6;
    const x1 = X_OWNER + 200;
    const x2 = X_OUTLET;
    const path = svg("path", {
      d: `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`,
      class: "own-link", "data-owner": l.owner, "data-outlet": l.outlet,
      "stroke-width": 1 + 3 * Math.min(1, (l.share || 50) / 100),
    });
    gLinks.append(path);
    if (l.share) {
      gLinks.append(svg("text", { x: x2 - 8, y: pos[`t:${l.outlet}`] + k * 11 + (siblings.length > 1 ? 3 : -4),
        class: "own-share", "text-anchor": "end",
        "data-owner": l.owner, "data-outlet": l.outlet }, `${String(l.share).replace(".", ",")}%`));
    }
  }
  const gNodes = svg("g");
  owners.forEach((o) => {
    const g = svg("g", { class: `own-node owner k-${o.kind}`, "data-node": o.id, tabindex: 0,
      transform: `translate(${X_OWNER},${pos[`o:${o.id}`]})` });
    g.append(svg("rect", { x: 0, y: -12, width: 200, height: 24, rx: 12 }),
      svg("text", { x: 12, y: 4 }, clip(o.name, 30)));
    g.addEventListener("click", () => showOwner(o, data));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") showOwner(o, data); });
    g.addEventListener("pointerenter", () => highlightOwn(svgEl, { owner: o.id }));
    g.addEventListener("pointerleave", () => highlightOwn(svgEl, null));
    gNodes.append(g);
  });
  outlets.forEach((o) => {
    const g = svg("g", { class: `own-node outlet${o.in_app ? "" : " not-in-app"}${o.agency ? " agency" : ""}`,
      "data-node": o.id, tabindex: 0, transform: `translate(${X_OUTLET},${pos[`t:${o.id}`]})` });
    g.append(svg("rect", { x: 0, y: -12, width: 240, height: 24, rx: 6 }),
      svg("text", { x: 10, y: 4 }, clip(o.name, o.agency ? 24 : 34) + (o.agency ? " · agência" : "")));
    const go = () => { location.hash = `#investigar/jornais/${o.id}`; };
    g.addEventListener("click", go);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    g.addEventListener("pointerenter", (e) => {
      highlightOwn(svgEl, { outlet: o.id });
      showTip(e, [o.name, o.type || "", o.in_app ? "Clique para investigar" : "Não está nos feeds da app"]);
    });
    g.addEventListener("pointerleave", () => { highlightOwn(svgEl, null); hideTip(); });
    gNodes.append(g);
  });
  svgEl.append(gLinks, gNodes);
  svgEl.style.aspectRatio = `${W} / ${H}`;   // the whole map fits the width, no inner scrolling
  const pz = panZoom(svgEl, { x: 0, y: 0, w: W, h: H });
  zoomButtons(graphBox, pz);

  const legend = $("#own-legend");
  legend.replaceChildren(...Object.entries(OWNER_KIND_LABELS)
    .filter(([k]) => owners.some((o) => o.kind === k))
    .map(([k, label]) => el("span", { className: `k-${k}` }, label)));
}

function highlightOwn(svgEl, focus) {
  svgEl.classList.toggle("focusing", !!focus);
  for (const node of svgEl.querySelectorAll("[data-owner]")) {
    const on = focus && (node.dataset.owner === focus.owner || node.dataset.outlet === focus.outlet);
    node.classList.toggle("on", !!on);
  }
}

function showOwner(owner, data) {
  const box = $("#own-detail");
  const links = data.links.filter((l) => l.owner === owner.id);
  const names = Object.fromEntries(data.outlets.map((o) => [o.id, o.name]));
  box.replaceChildren(
    el("h3", {}, owner.name),
    el("p", { className: "dd-muted" }, OWNER_KIND_LABELS[owner.kind] || owner.kind || ""));
  const ul = el("ul");
  for (const l of links) {
    const li = el("li");
    const a = el("a", { href: `#investigar/jornais/${l.outlet}` }, names[l.outlet] || l.outlet);
    li.append(a, l.share ? ` — ${String(l.share).replace(".", ",")}%` : "", l.via ? ` (via ${l.via})` : "");
    if (l.source) {
      li.append(" ", el("a", { href: l.source, target: "_blank", rel: "noopener noreferrer",
        className: "whois-src" }, hostOf(l.source)));
    }
    ul.append(li);
  }
  box.append(ul);
  box.hidden = false;
  box.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ==========================================================================
// Outlets / journalists: picker, summary, findings, radial graph
// ==========================================================================
const ddSearch = $("#dd-search");
const ddList = $("#dd-list");
const ddResult = $("#dd-result");

// Phones: once something is chosen, the list folds into a "Mudar de…" button.
const ddPicker = $(".dd-picker");
const ddPickerToggle = $(".dd-picker-toggle");
ddPickerToggle.addEventListener("click", () => {
  const open = !ddPicker.classList.contains("open");
  ddPicker.classList.toggle("open", open);
  ddPickerToggle.setAttribute("aria-expanded", String(open));
  if (open) ddSearch.focus();
});

async function showEntityTab(kind, id, changedTab) {
  ddSearch.placeholder = kind === "outlet" ? "Procurar jornal ou agência…" : "Procurar jornalista…";
  ddPicker.classList.toggle("has-choice", !!id);
  ddPicker.classList.remove("open");
  ddPickerToggle.setAttribute("aria-expanded", "false");
  ddPickerToggle.firstElementChild.textContent = kind === "outlet" ? "Mudar de jornal" : "Mudar de jornalista";
  if (changedTab) {
    ddSearch.value = "";
    if (!ddState.index[kind]) {
      ddList.replaceChildren(el("li", { className: "dd-muted" }, "A carregar…"));
      ddState.index[kind] = await (await fetch(`/api/deepdive/index?kind=${kind}`)).json();
    }
  }
  renderPicker(kind);
  if (id) await showEntity(kind, id);
  else {
    ddResult.replaceChildren(el("p", { className: "dd-empty" },
      kind === "outlet" ? "Escolha um jornal ou agência na lista."
        : "Escolha um jornalista na lista. Os que têm perfil investigado estão marcados com ●."));
  }
}

function renderPicker(kind) {
  const q = ddSearch.value.trim().toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
  const all = ddState.index[kind] || [];
  const match = all.filter((e) => !q || e.name.toLowerCase().normalize("NFD")
    .replace(/\p{M}/gu, "").includes(q));
  ddList.replaceChildren();
  const max = kind === "outlet" ? 40 : 60;
  for (const e of match.slice(0, max)) {
    const a = el("a", { href: `#investigar/${TAB_OF_KIND[kind]}/${e.id}`,
      className: e.id === ddState.id ? "active" : "" });
    a.append(el("span", { className: e.has_profile ? "dd-dot on" : "dd-dot",
      title: e.has_profile ? "Tem perfil investigado" : "Sem perfil investigado" }),
      el("span", { className: "dd-name" }, e.name),
      el("span", { className: "dd-count" }, String(e.articles)));
    if (e.outlets?.length) a.title = e.outlets.join(", ");
    const li = el("li");
    li.append(a);
    ddList.append(li);
  }
  if (match.length > max) {
    ddList.append(el("li", { className: "dd-muted" }, `+${match.length - max} — escreva para filtrar`));
  }
  if (!match.length) ddList.append(el("li", { className: "dd-muted" }, "Nenhum resultado."));
}
ddSearch.addEventListener("input", () => renderPicker(KIND_OF_TAB[ddState.tab]));

async function showEntity(kind, id) {
  ddResult.replaceChildren(el("p", { className: "dd-muted" }, "A analisar…"));
  const res = await fetch(`/api/deepdive/${kind}/${encodeURIComponent(id)}`);
  if (!res.ok) {
    ddResult.replaceChildren(el("p", { className: "dd-empty" }, "Sem dados para esta escolha."));
    return;
  }
  const d = await res.json();
  if (ddState.id !== id) return;                // another choice was made meanwhile
  renderEntity(d);
}

function renderEntity(d) {
  ddResult.replaceChildren();
  const head = el("header", { className: "dd-head" });
  head.append(el("h2", {}, d.name));
  if (d.role) head.append(el("p", { className: "dd-role" }, d.role));
  // the key numbers as tiles
  const pct = (k) => (d.articles ? Math.round(100 * d.labels[k] / d.articles) : 0);
  const stats = el("div", { className: "dd-stats" });
  const stat = (value, label, cls = "") => {
    const tile = el("div", { className: `dd-stat ${cls}` });
    tile.append(el("b", {}, value), el("span", {}, label));
    stats.append(tile);
  };
  stat(String(d.articles), `${d.articles === 1 ? "notícia" : "notícias"} · ${d.window_days} dias`);
  if (d.articles) {
    stat(`${pct("positive")}%`, "positivas", "pos");
    stat(`${pct("negative")}%`, "negativas", "neg");
  }
  if (d.kind === "journalist" && d.outlets.length) stat(d.outlets.join(", "), "escreve em", "wide");
  head.append(stats);
  if (d.stale) head.append(el("p", { className: "whois-checked stale" }, "Perfil pode estar desatualizado."));
  ddResult.append(head, el("p", { className: "dd-summary" }, d.summary));

  if (!d.findings.length) {
    ddResult.append(el("p", { className: "dd-empty" },
      d.has_profile ? "Sem findings para mostrar." : "Ainda sem perfil investigado e sem notícias suficientes."));
    return;
  }

  const cards = el("div", { className: "dd-findings" });
  ddResult.append(el("h3", { className: "dd-h3" }, "O que se encontrou"), cards);
  const cardById = {};
  for (const f of d.findings) {
    const card = el("section", { className: `dd-card ${FINDING_CLASS[f.kind] || ""}`, tabIndex: 0 });
    card.dataset.finding = f.id;
    const h4 = el("h4");
    const badge = el("span", { className: "dd-ficon" });
    badge.append(icon(FINDING_ICON[f.kind] || "info", 16));
    h4.append(badge, el("span", {}, f.title));
    card.append(h4, el("p", {}, f.text));
    if (f.sources?.length) {
      const src = el("p", { className: "dd-sources" }, "Fontes: ");
      f.sources.forEach((u) => src.append(el("a", { href: u, target: "_blank", rel: "noopener noreferrer",
        className: "whois-src" }, hostOf(u)), " "));
      card.append(src);
    }
    if (f.total) {
      const det = el("details");
      det.append(el("summary", {}, `Ver ${f.total === 1 ? "a notícia" : `as ${f.total} notícias`}`
        + (f.total > f.articles.length ? ` (primeiras ${f.articles.length})` : "")));
      const ul = el("ul", { className: "dd-articles" });
      for (const a of f.articles) {
        const li = el("li", { className: a.label });
        li.append(el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer" }, a.title),
          el("small", {}, ` · ${a.source_name} · ${dayMonth(a.published_at)}`));
        ul.append(li);
      }
      det.append(ul);
      card.append(det);
    }
    cardById[f.id] = card;
    cards.append(card);
  }
  // The findings first; the map of what supports them after, folded on phones.
  const map = el("details", { className: "dd-map", open: window.innerWidth > 760 });
  const graphWrap = el("div", { className: "dd-graph dd-graph-radial" });
  const svgEl = svg("svg", { role: "img", "aria-label": `Mapa dos findings de ${d.name}` });
  graphWrap.append(svgEl);
  map.append(el("summary", { className: "dd-h3" }, "Mapa: o que sustenta cada conclusão"),
    el("p", { className: "dd-muted dd-graph-hint" },
      "Arraste para mover, use a roda do rato para aproximar. Clique num finding para o destacar e numa notícia para a abrir."),
    graphWrap);
  ddResult.append(map);
  for (const card of cards.children) {
    card.addEventListener("click", (e) => {
      if (e.target.closest("a, summary")) return;
      focusFinding(svgEl, cards, card.dataset.finding);
    });
  }
  drawRadial(svgEl, graphWrap, d, cards);
}

function focusFinding(svgEl, cards, id) {
  const already = svgEl.dataset.focus === id;
  const next = already ? "" : id;
  svgEl.dataset.focus = next;
  svgEl.classList.toggle("focusing", !!next);
  for (const n of svgEl.querySelectorAll("[data-f]")) n.classList.toggle("on", n.dataset.f === next);
  for (const c of cards.children) c.classList.toggle("on", c.dataset.finding === next);
}

function drawRadial(svgEl, wrap, d, cards) {
  const R1 = 165;          // findings
  const R2 = 275;          // their articles / sources
  const PER = 12;          // article nodes per finding
  const F = d.findings.length;
  const g = svg("g");
  const gLinks = svg("g", { class: "rad-links" });
  const gNodes = svg("g");
  g.append(gLinks, gNodes);

  d.findings.forEach((f, i) => {
    const angle = -Math.PI / 2 + i * 2 * Math.PI / F;
    const fx = R1 * Math.cos(angle);
    const fy = R1 * Math.sin(angle);
    gLinks.append(svg("line", { x1: 0, y1: 0, x2: fx, y2: fy, class: "rad-link", "data-f": f.id }));

    // leaves: articles (data findings) or sources (researched findings)
    const leaves = f.sources?.length
      ? f.sources.map((u) => ({ kind: "source", url: u, title: hostOf(u) }))
      : f.articles.slice(0, PER).map((a) => ({ kind: "article", ...a }));
    const more = f.total ? f.total - leaves.length : 0;
    const all = more > 0 ? [...leaves, { kind: "more", title: `+${more}` }] : leaves;
    const spread = Math.min(Math.PI * 2 / F * 0.8, 1.2);
    all.forEach((leaf, j) => {
      const t = all.length === 1 ? 0 : j / (all.length - 1) - 0.5;
      const a = angle + t * spread;
      const r = R2 + (j % 2 ? 30 : 0);
      const lx = r * Math.cos(a);
      const ly = r * Math.sin(a);
      gLinks.append(svg("line", { x1: fx, y1: fy, x2: lx, y2: ly, class: "rad-link leaf", "data-f": f.id }));
      const node = svg("g", { class: `rad-leaf ${leaf.kind} ${leaf.label || ""}`, "data-node": "1",
        "data-f": f.id, transform: `translate(${lx},${ly})`, tabindex: leaf.url ? 0 : -1 });
      if (leaf.kind === "source") node.append(svg("rect", { x: -9, y: -9, width: 18, height: 18, rx: 3 }));
      else if (leaf.kind === "more") node.append(svg("circle", { r: 17 }), svg("text", { y: 5, "text-anchor": "middle" }, leaf.title));
      else node.append(svg("circle", { r: 9 }));
      if (leaf.url) {
        const open = () => window.open(leaf.url, "_blank", "noopener");
        node.addEventListener("click", open);
        node.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      }
      node.addEventListener("pointerenter", (e) => showTip(e, leaf.kind === "source"
        ? [leaf.title, "Fonte da pesquisa — clique para abrir"]
        : leaf.kind === "more" ? [`Mais ${more} notícias`, "Veja-as no cartão do finding"]
          : [leaf.title, `${leaf.source_name} · ${dayMonth(leaf.published_at)}`,
            `Tom: ${(LABELS[leaf.label] || leaf.label || "").toLowerCase()}`]));
      node.addEventListener("pointerleave", hideTip);
      gNodes.append(node);
    });

    const fn = svg("g", { class: `rad-finding ${FINDING_CLASS[f.kind] || ""}`, "data-node": "1",
      "data-f": f.id, tabindex: 0, transform: `translate(${fx},${fy})` });
    const anchor = Math.cos(angle) < -0.2 ? "end" : Math.cos(angle) > 0.2 ? "start" : "middle";
    const tx = anchor === "end" ? -28 : anchor === "start" ? 28 : 0;
    const ty = anchor === "middle" ? (Math.sin(angle) < 0 ? -32 : 42) : 7;
    // the kind's line icon, centred in the circle
    const glyph = icon(FINDING_ICON[f.kind] || "info", 22);
    glyph.setAttribute("x", -11);
    glyph.setAttribute("y", -11);
    glyph.classList.add("rad-glyph");
    fn.append(svg("circle", { r: 21 }), glyph,
      svg("text", { x: tx, y: ty, "text-anchor": anchor, class: "rad-label" }, clip(f.title, 26)));
    fn.addEventListener("click", () => focusFinding(svgEl, cards, f.id));
    fn.addEventListener("keydown", (e) => { if (e.key === "Enter") focusFinding(svgEl, cards, f.id); });
    fn.addEventListener("pointerenter", (e) => showTip(e, [f.title, clip(f.text, 160)]));
    fn.addEventListener("pointerleave", hideTip);
    gNodes.append(fn);
  });

  const center = svg("g", { class: `rad-center ${d.kind}`, "data-node": "1" });
  center.append(svg("circle", { r: 62 }));
  const words = d.name.split(" ");
  const lines = [];
  for (const w of words) {
    if (lines.length && (lines[lines.length - 1] + " " + w).length <= 12) lines[lines.length - 1] += ` ${w}`;
    else lines.push(w);
  }
  lines.slice(0, 3).forEach((l, i, arr) => center.append(svg("text",
    { y: (i - (arr.length - 1) / 2) * 21 + 7, "text-anchor": "middle" }, clip(l, 14))));
  gNodes.append(center);

  svgEl.append(g);
  const size = R2 + 170;
  const side = size + 110;   // room for the labels of the left and right findings
  const pz = panZoom(svgEl, { x: -side, y: -size * 0.85, w: side * 2, h: size * 1.7 });
  zoomButtons(wrap, pz);
}

// ==========================================================================
// Silences: topics x outlets
// ==========================================================================
async function showSilences() {
  for (const b of document.querySelectorAll("[data-silence-scope]")) {
    b.setAttribute("aria-pressed", String(b.dataset.silenceScope === ddState.silenceScope));
  }
  const table = $("#silence-table");
  table.replaceChildren(el("caption", {}, "A carregar…"));
  $("#silence-detail").hidden = true;
  const data = await (await fetch(`/api/silences?scope=${ddState.silenceScope}`)).json();
  table.replaceChildren();
  const wrap = table.parentElement;
  wrap.querySelector(".dd-empty")?.remove();
  table.hidden = !data.topics.length;
  if (!data.topics.length) {
    // outside the table: a caption is as narrow as the (empty) table
    wrap.append(el("p", { className: "dd-empty" },
      "Ainda há poucos temas no arquivo para comparar. A tabela enche com o tempo, à medida que a app guarda notícias."));
    return;
  }
  table.append(el("caption", {},
    `Últimos ${data.window_days} dias · jornais generalistas com pelo menos `
    + `${data.thresholds.silence_min_outlet_articles} notícias (os independentes têm outra missão e ficam de fora)`));
  const thead = el("thead");
  const hr = el("tr");
  hr.append(el("th", { scope: "col" }, "Tema"));
  for (const o of data.outlets) hr.append(el("th", { scope: "col", title: `${o.articles} notícias` }, o.name));
  thead.append(hr);
  const tbody = el("tbody");
  for (const t of data.topics) {
    const tr = el("tr");
    tr.append(el("th", { scope: "row" }, t.label));
    for (const o of data.outlets) {
      const c = t.cells[o.id];
      const ratio = c.ratio ?? 1;
      const level = c.silent ? "silent" : ratio < 0.6 ? "low" : ratio > 1.6 ? "high" : "mid";
      const td = el("td", { className: `cell ${level}`, tabIndex: 0 });
      if (c.silent) td.append(icon("mute", 13));
      td.append(String(c.count));
      td.title = `${o.name}: ${c.count} notícias sobre ${t.label} (esperadas ~${String(c.expected).replace(".", ",")})`;
      const open = () => showSilenceCell(t, o, c);
      td.addEventListener("click", open);
      td.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
}

async function showSilenceCell(topic, outlet, cell) {
  const box = $("#silence-detail");
  box.hidden = false;
  box.replaceChildren(el("h3", {}, `${topic.label} em ${outlet.name}`),
    el("p", {}, `${cell.count} notícias, face a ~${String(cell.expected).replace(".", ",")} esperadas `
      + `pelo volume do jornal. ${topic.covered_by} jornais cobriram o tema.`));
  const params = new URLSearchParams({ topic: topic.slug, scope: ddState.silenceScope });
  const own = cell.count ? await (await fetch(`/api/silences/articles?${params}&outlet=${outlet.id}`)).json() : [];
  const all = await (await fetch(`/api/silences/articles?${params}`)).json();
  const list = (title, items) => {
    box.append(el("h4", {}, title));
    const ul = el("ul", { className: "dd-articles" });
    for (const a of items) {
      const li = el("li", { className: a.label });
      li.append(el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer" }, a.title),
        el("small", {}, ` · ${a.source_name} · ${dayMonth(a.published_at)}`));
      ul.append(li);
    }
    box.append(ul);
  };
  if (own.length) list(`O que ${outlet.name} publicou`, own);
  list("O que os outros publicaram", all.filter((a) => a.source !== outlet.id).slice(0, 15));
  box.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

for (const b of document.querySelectorAll("[data-silence-scope]")) {
  b.addEventListener("click", () => {
    ddState.silenceScope = b.dataset.silenceScope;
    showSilences();
  });
}
