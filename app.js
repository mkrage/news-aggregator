const STORAGE_KEY = "news-aggregator-read";
const SCROLLED_KEY = "news-aggregator-scrolled";
const PREFS_KEY = "news-aggregator-prefs";
// Bis v1 wurde das Theme bei jeder Änderung mitgeschrieben, auch wenn es nur
// die Vorgabe war. Ein Stand ohne diese Marke sagt darum nichts darüber, ob das
// Theme bewusst gewählt wurde.
const PREFS_VERSION = 2;
const PREVIEW_LENGTH = 280;
const RETENTION_DAYS = 30;
const SEARCH_DEBOUNCE_MS = 150;
const AI_SUMMARY_MAX_AGE_HOURS = 24;

const THEMES = ["app", "editorial"];
// Kachel/Liste am breiten Bildschirm, Dichte am schmalen: einspaltig sehen
// Kacheln und Liste gleich aus, dort ist die Frage eine andere.
const VIEWS = ["grid", "list"];
const DENSITIES = ["compact", "cards", "large"];

// Ab dieser Breite ist das Zeitungslayout die Voreinstellung. Muss zur
// gleichnamigen Abfrage im Inline-Skript in index.html passen.
const WIDE_SCREEN_QUERY = "(min-width: 64rem)";

// Muss zur Masthead-Fläche (--surface) des jeweiligen Themes passen, damit die
// Browserleiste auf Mobilgeräten nicht aus dem Rahmen fällt.
const THEME_COLORS = {
  app: { light: "#ffffff", dark: "#141a24" },
  editorial: { light: "#faf7f2", dark: "#17140f" },
};

const EDITORIAL_FONT_HREF =
  "https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&display=swap";

const state = {
  all: [],
  top: [],
  aiSummary: null,
  tab: "top",
  view: "grid",
  density: "cards",
  theme: "app",
  mode: "light",
  // Solange der Modus nicht bewusst gewählt wurde, folgt er dem System,
  // das Theme der Bildschirmbreite.
  modeExplicit: false,
  themeExplicit: false,
  read: new Map(), // id -> Zeitstempel, explizit markiert
  scrolled: new Map(), // id -> Zeitstempel, beim Scrollen erfasst
  // Snapshot beim Laden: nur diese Artikel blendet "Gelesene ausblenden" aus.
  // Was während der Sitzung gelesen wird, bleibt sichtbar (nur markiert) und
  // fliegt erst beim nächsten Laden raus.
  hiddenAtLoad: new Set(),
  // Artikel, die in dieser Sitzung bewusst auf "ungelesen" gesetzt wurden,
  // sollen nicht durch Scrollen sofort wieder als gelesen gelten.
  keepUnread: new Set(),
};

const dom = {};
let observers = [];
let searchTimer = null;
let editorialFontRequested = false;

// Service Worker registrieren
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(console.error);
}

/* ---------- Speicher ---------- */

function readJson(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn("Einstellungen konnten nicht gespeichert werden", error);
  }
}

// Lesestatus mit Zeitstempel, damit alte Einträge irgendwann wegfallen.
// Das alte Format war ein reines Array von IDs und wird migriert.
function loadMarks(key) {
  const raw = readJson(key);
  const now = Date.now();

  if (Array.isArray(raw)) {
    return new Map(raw.filter((id) => typeof id === "string").map((id) => [id, now]));
  }
  if (!raw || typeof raw !== "object") return new Map();

  const cutoff = now - RETENTION_DAYS * 24 * 60 * 60 * 1000;
  return new Map(
    Object.entries(raw).filter(([, ts]) => typeof ts === "number" && ts > cutoff)
  );
}

function saveMarks(key, marks) {
  writeJson(key, Object.fromEntries(marks));
}

/* ---------- Darstellung ---------- */

function systemPrefersDark() {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}

// Am großen Bildschirm ist die Zeitung die schönere Vorgabe, am Telefon die
// kompakte App-Ansicht. Nur eine Vorgabe: eine eigene Wahl gilt überall.
function prefersEditorial() {
  return typeof window.matchMedia === "function"
    ? window.matchMedia(WIDE_SCREEN_QUERY).matches
    : false;
}

function loadPrefs() {
  const prefs = readJson(PREFS_KEY) || {};

  // Ohne Versionsmarke gilt ein gespeichertes Theme nicht als eigene Wahl –
  // dann greift wieder die Vorgabe nach Bildschirmbreite.
  state.themeExplicit = prefs.v >= PREFS_VERSION && THEMES.includes(prefs.theme);
  state.theme = state.themeExplicit
    ? prefs.theme
    : prefersEditorial()
      ? "editorial"
      : "app";
  state.view = VIEWS.includes(prefs.view) ? prefs.view : "grid";
  state.density = DENSITIES.includes(prefs.density) ? prefs.density : "cards";
  state.modeExplicit = prefs.mode === "light" || prefs.mode === "dark";
  state.mode = state.modeExplicit ? prefs.mode : systemPrefersDark() ? "dark" : "light";
}

function savePrefs() {
  // Nur bewusst Gewähltes festschreiben – sonst friert die erste Änderung an
  // Modus oder Layout die geräteabhängige Theme-Vorgabe ein.
  const prefs = { v: PREFS_VERSION, view: state.view, density: state.density };
  if (state.themeExplicit) prefs.theme = state.theme;
  if (state.modeExplicit) prefs.mode = state.mode;
  writeJson(PREFS_KEY, prefs);
}

// Die Serif-Schrift wird erst geholt, wenn das Zeitungs-Theme wirklich zum
// Einsatz kommt – App-Nutzer zahlen dafür keinen Request.
function ensureEditorialFont() {
  if (editorialFontRequested) return;
  editorialFontRequested = true;

  const preconnect = document.createElement("link");
  preconnect.rel = "preconnect";
  preconnect.href = "https://fonts.gstatic.com";
  preconnect.crossOrigin = "anonymous";

  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = EDITORIAL_FONT_HREF;

  document.head.append(preconnect, stylesheet);
}

function applyAppearance() {
  const root = document.documentElement;
  root.dataset.theme = state.theme;
  root.dataset.mode = state.mode;

  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.content = THEME_COLORS[state.theme][state.mode];

  document.querySelectorAll(".theme-btn").forEach((btn) => {
    const active = btn.dataset.themeChoice === state.theme;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", String(active));
  });

  document.querySelectorAll(".view-btn").forEach((btn) => {
    const active = btn.dataset.view === state.view;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });

  document.querySelectorAll(".density-btn").forEach((btn) => {
    const active = btn.dataset.density === state.density;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });

  if (dom.modeToggle) {
    const toDark = state.mode === "light";
    dom.modeToggle.title = toDark ? "Dunkelmodus einschalten" : "Hellmodus einschalten";
    dom.modeToggle.setAttribute("aria-label", dom.modeToggle.title);
  }

  if (state.theme === "editorial") ensureEditorialFont();
}

function setTheme(theme) {
  if (!THEMES.includes(theme)) return;
  const changed = theme !== state.theme;
  state.theme = theme;
  state.themeExplicit = true;
  savePrefs();
  if (!changed) return;
  applyAppearance();
}

function setMode(mode, explicit = true) {
  state.mode = mode;
  if (explicit) state.modeExplicit = true;
  savePrefs();
  applyAppearance();
}

function setView(view) {
  if (!VIEWS.includes(view) || view === state.view) return;
  state.view = view;
  savePrefs();
  applyAppearance();
  render();
}

function setDensity(density) {
  if (!DENSITIES.includes(density) || density === state.density) return;
  state.density = density;
  savePrefs();
  applyAppearance();
  render();
}

/* ---------- Lesestatus ---------- */

function isRead(id) {
  return state.read.has(id) || state.scrolled.has(id);
}

function markAsRead(id) {
  if (state.read.has(id)) return;
  state.read.set(id, Date.now());
  state.keepUnread.delete(id);
  saveMarks(STORAGE_KEY, state.read);
}

function markAsUnread(id) {
  state.read.delete(id);
  state.scrolled.delete(id);
  state.keepUnread.add(id);
  saveMarks(STORAGE_KEY, state.read);
  saveMarks(SCROLLED_KEY, state.scrolled);
}

function markAsScrolled(id) {
  if (state.scrolled.has(id) || state.keepUnread.has(id)) return false;
  state.scrolled.set(id, Date.now());
  saveMarks(SCROLLED_KEY, state.scrolled);
  return true;
}

/* ---------- Hilfsfunktionen ---------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function svgIcon(pathData, className) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);

  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", pathData);
  svg.appendChild(path);
  return svg;
}

const CHECK_PATH = "M20 6 9 17l-5-5";

// Nur absolute http(s)-URLs zulassen: das schließt "javascript:" aus und
// verhindert, dass ein Müllwert relativ zur eigenen Seite aufgelöst wird.
function safeUrl(value) {
  if (!value || typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function parseDate(isoString) {
  if (!isoString) return null;
  const date = new Date(isoString);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(isoString) {
  const date = parseDate(isoString);
  if (!date) return "unbekannt";
  return date.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timeAgo(isoString) {
  const date = parseDate(isoString);
  if (!date) return "unbekannt";

  const diffMins = Math.floor((Date.now() - date.getTime()) / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 5) return "gerade eben";
  if (diffMins < 60) return `vor ${diffMins} Min`;
  if (diffHours < 24) return `vor ${diffHours} Std`;
  if (diffDays === 1) return "vor 1 Tag";
  return `vor ${diffDays} Tagen`;
}

/* ---------- Daten laden ---------- */

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function loadData() {
  state.read = loadMarks(STORAGE_KEY);
  state.scrolled = loadMarks(SCROLLED_KEY);
  saveMarks(STORAGE_KEY, state.read);
  saveMarks(SCROLLED_KEY, state.scrolled);
  state.hiddenAtLoad = new Set([...state.read.keys(), ...state.scrolled.keys()]);

  try {
    const [newsData, topData, aiData] = await Promise.all([
      fetchJson("data/news.json"),
      fetchJson("data/top-news.json"),
      fetchJson("data/ai-summary.json").catch(() => null),
    ]);

    state.all = Array.isArray(newsData.articles) ? newsData.articles : [];
    state.top = Array.isArray(topData.articles) ? topData.articles : [];
    state.aiSummary = isFreshSummary(aiData) ? aiData : null;

    dom.lastUpdated.textContent = `Aktualisiert ${formatDate(newsData.generatedAt)}`;

    populateFilters();
    render();
  } catch (error) {
    console.error(error);
    dom.lastUpdated.textContent = "Aktualisierung unbekannt";
    dom.newsList.replaceChildren(
      el("p", "empty", "Fehler beim Laden der News. Bitte später erneut versuchen.")
    );
  }
}

// Eine alte Zusammenfassung ist schlechter als keine.
function isFreshSummary(data) {
  if (!data || typeof data.summary !== "string" || !data.summary.trim()) return false;
  const generated = parseDate(data.generatedAt);
  if (!generated) return false;
  return Date.now() - generated.getTime() < AI_SUMMARY_MAX_AGE_HOURS * 60 * 60 * 1000;
}

function populateFilters() {
  fillSelect(
    dom.sourceFilter,
    "Alle Quellen",
    state.all.map((a) => a.source)
  );
  fillSelect(
    dom.categoryFilter,
    "Alle Kategorien",
    state.all.map((a) => a.category)
  );
}

function fillSelect(select, placeholder, values) {
  const previous = select.value;
  const options = [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "de"));

  const placeholderOption = el("option", null, placeholder);
  placeholderOption.value = "";
  select.replaceChildren(placeholderOption);

  options.forEach((value) => {
    const option = el("option", null, value);
    option.value = value;
    select.appendChild(option);
  });

  if (options.includes(previous)) select.value = previous;
}

/* ---------- Filtern ---------- */

function getFilteredArticles() {
  const search = dom.search.value.trim().toLowerCase();
  const source = dom.sourceFilter.value;
  const category = dom.categoryFilter.value;
  // Im Gelesen-Tab nie ausblenden – dort ist "gelesen" ja das Kriterium.
  const hideRead = state.tab !== "read" && dom.hideRead.checked;

  let articles;
  if (state.tab === "read") {
    articles = state.all.filter((article) => isRead(article.id));
  } else {
    articles = state.tab === "top" ? state.top : state.all;
  }

  return articles.filter((article) => {
    if (source && article.source !== source) return false;
    if (category && article.category !== category) return false;
    if (hideRead && state.hiddenAtLoad.has(article.id)) return false;
    if (search) {
      const text = [article.title, article.summary, article.source]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!text.includes(search)) return false;
    }
    return true;
  });
}

/* ---------- Rendern ---------- */

function render() {
  renderAiSummary();

  // Observer der vorherigen Liste abräumen, sonst sammeln sie sich auf.
  observers.forEach((observer) => observer.disconnect());
  observers = [];

  const articles = getFilteredArticles();
  if (articles.length === 0) {
    dom.newsList.className = "news-list";
    dom.newsList.replaceChildren(el("p", "empty", "Keine News gefunden."));
    return;
  }

  // Beide Klassen sind immer gesetzt; welche greift, entscheidet die
  // Bildschirmbreite im Stylesheet.
  const isGrid = state.view === "grid";
  dom.newsList.className = `news-list ${isGrid ? "top-view" : "list-view"} density-${state.density}`;

  // Bezugsgröße für den Relevanzbalken: der stärkste Artikel der Auswahl.
  const maxScore = Math.max(
    ...articles.map((a) => (typeof a.score === "number" ? a.score : 0)),
    1
  );

  dom.newsList.replaceChildren(...articles.map((article) => buildCard(article, maxScore)));
}

function renderAiSummary() {
  if (state.tab !== "top" || !state.aiSummary) {
    dom.aiSummary.classList.add("hidden");
    dom.aiSummaryContent.replaceChildren();
    return;
  }

  const paragraphs = state.aiSummary.summary
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => el("p", null, line));

  dom.aiSummaryContent.replaceChildren(...paragraphs);
  dom.aiSummary.classList.remove("hidden");
}

function buildCard(article, maxScore) {
  const item = el("article", "news-item");
  item.dataset.id = article.id;

  const imageUrl = safeUrl(article.image);
  if (imageUrl) {
    const img = el("img", "news-image");
    img.src = imageUrl;
    img.alt = "";
    img.loading = "lazy";
    // Kaputte Bild-URLs sollen kein Loch in die Kachel reißen.
    img.addEventListener("error", () => img.replaceWith(buildImagePlaceholder()), { once: true });
    item.appendChild(img);
  } else {
    item.appendChild(buildImagePlaceholder());
  }

  const content = el("div", "news-content");
  content.appendChild(buildMeta(article, maxScore));
  content.appendChild(buildTitle(article, item));
  content.appendChild(buildSummary(article));

  const coverage = buildCoverage(article);
  if (coverage) content.appendChild(coverage);

  const toggleRead = el("button", "toggle-read");
  toggleRead.addEventListener("click", () => {
    if (isRead(article.id)) {
      markAsUnread(article.id);
    } else {
      markAsRead(article.id);
    }
    applyReadState(item, article.id);
  });

  const actions = el("div", "news-actions");
  actions.appendChild(toggleRead);
  content.appendChild(actions);
  item.appendChild(content);

  applyReadState(item, article.id);
  observeScrolled(item, article.id);
  return item;
}

function buildImagePlaceholder() {
  return el("div", "news-image-placeholder", "📰");
}

function buildMeta(article, maxScore) {
  const meta = el("div", "news-meta");

  const source = el("span", "news-source", article.source || "unbekannt");
  if (article.source) source.dataset.source = article.source;
  meta.appendChild(source);

  if (article.category) meta.appendChild(el("span", "news-category", article.category));

  // Immer im DOM, sichtbar macht ihn erst die Klasse "read" per CSS.
  const badge = el("span", "read-badge");
  badge.append(svgIcon(CHECK_PATH), el("span", null, "Gelesen"));
  meta.appendChild(badge);

  if (state.tab === "top" && typeof article.score === "number") {
    const heat = el("span", "news-heat");
    heat.style.setProperty("--heat", String(Math.min(article.score / maxScore, 1).toFixed(3)));
    heat.title = `Relevanz-Score ${article.score}`;
    meta.appendChild(heat);
  }

  const time = el("span", "news-time", timeAgo(article.published));
  time.title = formatDate(article.published);
  meta.appendChild(time);

  return meta;
}

function buildTitle(article, item) {
  const heading = el("h2", "news-title");
  const title = article.title || "Ohne Titel";
  const url = safeUrl(article.link);

  if (!url) {
    heading.textContent = title;
    return heading;
  }

  const link = el("a", null, title);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.addEventListener("click", () => {
    markAsRead(article.id);
    applyReadState(item, article.id);
  });

  heading.appendChild(link);
  return heading;
}

function buildSummary(article) {
  const summary = (article.summary || "").trim();
  const paragraph = el("p", "news-summary");

  if (summary.length <= PREVIEW_LENGTH) {
    paragraph.textContent = summary;
    return paragraph;
  }

  const preview = el("span", "summary-preview", `${summary.slice(0, PREVIEW_LENGTH).trim()}…`);
  const full = el("span", "summary-full hidden", summary);
  const toggle = el("button", "toggle-summary", "Mehr anzeigen");

  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    preview.classList.toggle("hidden", !expanded);
    full.classList.toggle("hidden", expanded);
    toggle.textContent = expanded ? "Mehr anzeigen" : "Weniger anzeigen";
  });

  paragraph.append(preview, full, toggle);
  return paragraph;
}

// Top-News zeigen pro Nachricht nur einen Artikel. Wer sonst noch darüber
// berichtet, steht hier – mit Link, damit die Auswahl nachvollziehbar bleibt.
function buildCoverage(article) {
  const coverage = article.coverage;
  const others = coverage && Array.isArray(coverage.others) ? coverage.others : [];
  if (others.length === 0) return null;

  const row = el("div", "news-coverage");
  row.appendChild(el("span", "coverage-label", "Auch bei"));

  others.forEach((other) => {
    const name = other && typeof other.source === "string" && other.source ? other.source : "unbekannt";
    const url = other ? safeUrl(other.link) : null;

    // news-source erbt die Farbe der Quelle, coverage-source nur das Verhalten.
    const chip = el(url ? "a" : "span", "news-source coverage-source", name);
    chip.dataset.source = name;

    if (url) {
      chip.href = url;
      chip.target = "_blank";
      chip.rel = "noopener noreferrer";
      if (typeof other.title === "string" && other.title) chip.title = other.title;
    }

    row.appendChild(chip);
  });

  return row;
}

// Nur die betroffene Kachel anfassen – kein Neuaufbau der ganzen Liste.
function applyReadState(item, id) {
  if (!item) return;
  const read = isRead(id);
  item.classList.toggle("read", read);

  const button = item.querySelector(".toggle-read");
  if (button) {
    button.replaceChildren();
    if (read) {
      button.textContent = "Als ungelesen markieren";
    } else {
      button.append(svgIcon(CHECK_PATH), el("span", null, "Als gelesen markieren"));
    }
  }

  // Im Gelesen-Tab gehört ein wieder ungelesener Artikel nicht mehr in die
  // Liste. isConnected: beim Aufbau einer Kachel ist sie noch nicht im DOM.
  if (state.tab === "read" && !read && item.isConnected) {
    item.remove();
    if (!dom.newsList.querySelector(".news-item")) {
      dom.newsList.className = "news-list";
      dom.newsList.replaceChildren(el("p", "empty", "Keine News gefunden."));
    }
  }
}

// Artikel als "überscrollt" merken, wenn er den Viewport nach oben verlässt.
// Bewusst ohne render(): die Kachel wird nur markiert, verschwindet aber erst
// beim nächsten Laden aus der Liste.
function observeScrolled(element, id) {
  if (!("IntersectionObserver" in window) || isRead(id)) return;

  let hasBeenVisible = false;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          hasBeenVisible = true;
          return;
        }
        if (hasBeenVisible && entry.boundingClientRect.top < 0) {
          if (markAsScrolled(id)) applyReadState(element, id);
          observer.disconnect();
          observers = observers.filter((other) => other !== observer);
        }
      });
    },
    { threshold: 0.1 }
  );

  observer.observe(element);
  observers.push(observer);
}

/* ---------- Initialisierung ---------- */

function cacheDom() {
  const ids = {
    lastUpdated: "last-updated",
    newsList: "news-list",
    aiSummary: "ai-summary",
    aiSummaryContent: "ai-summary-content",
    search: "search",
    sourceFilter: "source-filter",
    categoryFilter: "category-filter",
    hideRead: "hide-read",
    filterToggle: "filter-toggle",
    filtersPanel: "filters-panel",
    filterBadge: "filter-badge",
    modeToggle: "mode-toggle",
  };
  Object.entries(ids).forEach(([key, id]) => {
    dom[key] = document.getElementById(id);
  });
}

function updateFilterBadge() {
  const active = [dom.sourceFilter, dom.categoryFilter].filter((s) => s.value !== "").length;
  dom.filterBadge.textContent = String(active);
  dom.filterBadge.classList.toggle("hidden", active === 0);
}

function initAppearanceControls() {
  document.querySelectorAll(".theme-btn").forEach((btn) => {
    btn.addEventListener("click", () => setTheme(btn.dataset.themeChoice));
  });

  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  document.querySelectorAll(".density-btn").forEach((btn) => {
    btn.addEventListener("click", () => setDensity(btn.dataset.density));
  });

  if (dom.modeToggle) {
    dom.modeToggle.addEventListener("click", () => {
      setMode(state.mode === "dark" ? "light" : "dark");
    });
  }

  // Solange der Nutzer den Modus nicht selbst gewählt hat, dem System folgen.
  if (typeof window.matchMedia === "function") {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event) => {
      if (!state.modeExplicit) setMode(event.matches ? "dark" : "light", false);
    };
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
    } else if (typeof query.addListener === "function") {
      query.addListener(onChange);
    }
  }
}

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((other) => {
        other.classList.remove("active");
        other.setAttribute("aria-selected", "false");
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      dom.newsList.setAttribute("aria-labelledby", tab.id);
      state.tab = tab.dataset.tab;
      render();
    });
  });
}

function initFilters() {
  dom.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, SEARCH_DEBOUNCE_MS);
  });

  [dom.sourceFilter, dom.categoryFilter].forEach((select) => {
    select.addEventListener("change", () => {
      updateFilterBadge();
      render();
    });
  });

  dom.hideRead.addEventListener("change", render);
  updateFilterBadge();

  dom.filterToggle.addEventListener("click", () => {
    const open = dom.filtersPanel.classList.toggle("open");
    dom.filterToggle.classList.toggle("active", open);
    dom.filterToggle.setAttribute("aria-expanded", String(open));
  });
}

function init() {
  cacheDom();
  loadPrefs();
  applyAppearance();
  initAppearanceControls();
  initTabs();
  initFilters();
  loadData();
}

init();
