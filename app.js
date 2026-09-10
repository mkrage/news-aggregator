const STORAGE_KEY = "news-aggregator-read";
const SCROLLED_KEY = "news-aggregator-scrolled";
const PREVIEW_LENGTH = 280;
const RETENTION_DAYS = 30;
const SEARCH_DEBOUNCE_MS = 150;
const AI_SUMMARY_MAX_AGE_HOURS = 24;

const state = {
  all: [],
  top: [],
  aiSummary: null,
  tab: "top",
  view: "grid", // 'grid' oder 'list'
  read: new Map(), // id -> Zeitstempel, explizit markiert
  scrolled: new Map(), // id -> Zeitstempel, beim Scrollen erfasst
  // Snapshot beim Laden: nur diese Artikel blendet "Gelesene ausblenden" aus.
  // Was während der Sitzung gelesen wird, bleibt sichtbar (nur ausgegraut) und
  // fliegt erst beim nächsten Laden raus.
  hiddenAtLoad: new Set(),
  // Artikel, die in dieser Sitzung bewusst auf "ungelesen" gesetzt wurden,
  // sollen nicht durch Scrollen sofort wieder als gelesen gelten.
  keepUnread: new Set(),
};

const dom = {};
let observers = [];
let searchTimer = null;

// Service Worker registrieren
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(console.error);
}

/* ---------- Lesestatus ---------- */

// Speichert IDs mit Zeitstempel, damit alte Einträge irgendwann wegfallen.
// Das alte Format war ein reines Array von IDs und wird migriert.
function loadMarks(key) {
  let raw;
  try {
    raw = JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return new Map();
  }

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
  try {
    localStorage.setItem(key, JSON.stringify(Object.fromEntries(marks)));
  } catch (error) {
    console.warn("Lesestatus konnte nicht gespeichert werden", error);
  }
}

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

    dom.lastUpdated.textContent = `Letzte Aktualisierung: ${formatDate(newsData.generatedAt)}`;

    populateFilters();
    render();
  } catch (error) {
    console.error(error);
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

  const isGrid = state.view === "grid";
  dom.newsList.className = `news-list ${isGrid ? "top-view" : "list-view"}`;
  dom.newsList.replaceChildren(...articles.map((article) => buildCard(article, isGrid)));
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

function buildCard(article, isGrid) {
  const item = el("article", `news-item ${isGrid ? "grid-item" : "list-item"}`);
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
  content.appendChild(buildMeta(article));
  content.appendChild(buildTitle(article, item));
  content.appendChild(buildSummary(article));

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

function buildMeta(article) {
  const meta = el("div", "news-meta");

  const source = el("span", "news-source", article.source || "unbekannt");
  if (article.source) source.dataset.source = article.source;
  meta.appendChild(source);

  if (article.category) meta.appendChild(el("span", "news-category", article.category));

  const time = el("span", "news-time", timeAgo(article.published));
  time.title = formatDate(article.published);
  meta.appendChild(time);

  if (state.tab === "top" && typeof article.score === "number") {
    meta.appendChild(el("span", "news-score", `Score: ${article.score}`));
  }

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

// Nur die betroffene Kachel anfassen – kein Neuaufbau der ganzen Liste.
function applyReadState(item, id) {
  if (!item) return;
  const read = isRead(id);
  item.classList.toggle("read", read);

  const button = item.querySelector(".toggle-read");
  if (button) {
    button.textContent = read ? "Als ungelesen markieren" : "Als gelesen markieren";
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
// Bewusst ohne render(): die Kachel wird nur ausgegraut, verschwindet aber
// erst beim nächsten Laden aus der Liste.
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

function init() {
  cacheDom();

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

  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".view-btn").forEach((other) => {
        other.classList.remove("active");
        other.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");
      state.view = btn.dataset.view;
      render();
    });
  });

  loadData();
}

init();
