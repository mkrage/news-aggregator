/**
 * Frontend-Tests für app.js gegen ein jsdom-DOM.
 *
 * Ausführen: npm install && npm test
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(ROOT, "app.js"), "utf-8");
const news = JSON.parse(fs.readFileSync(path.join(ROOT, "data/news.json"), "utf-8"));
const top = JSON.parse(fs.readFileSync(path.join(ROOT, "data/top-news.json"), "utf-8"));

let failures = 0;
function check(label, condition, detail = "") {
  const ok = !!condition;
  if (!ok) failures++;
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${label}${detail && !ok ? ` -> ${detail}` : ""}`);
}
function section(title) {
  console.log(`\n${title}`);
}

/* ---------- Testdaten ---------- */

// Feindselige Feed-Werte: Titel mit Markup, javascript:-Link, Attribut-Ausbruch
// im Bild-URL-Feld.
const hostile = {
  id: "hostile-1",
  source: "heise",
  sourceWeight: 0.9,
  title: '<img src=x onerror=alert(1)>Bösartig" autofocus onfocus="alert(2)',
  link: 'javascript:alert("link")',
  summary: "kurz",
  image: 'x" onerror="alert(3)',
  published: new Date().toISOString(),
  category: "Technologie",
};
const hostileWithValidLink = {
  ...hostile,
  id: "hostile-2",
  link: "https://example.com/ok",
  image: null,
};

// Die echten Feeds liefern derzeit keine Zusammenfassung über PREVIEW_LENGTH,
// darum ein synthetischer Artikel für das Auf-/Zuklappen.
const longArticle = {
  id: "long-1",
  source: "golem",
  sourceWeight: 0.85,
  title: "Artikel mit langer Zusammenfassung",
  link: "https://example.com/lang",
  summary: `Anfang. ${"Ein ziemlich langer Satz zur Auffüllung der Zusammenfassung. ".repeat(8)}Ende.`,
  image: null,
  published: new Date().toISOString(),
  category: "Technologie",
};

// Eine Nachricht, die mehrere Portale melden: die Top-News zeigen einen
// Artikel und verlinken die weiteren Quellen. Ein Link ist feindselig.
const coveredArticle = {
  id: "covered-1",
  source: "tagesschau",
  sourceWeight: 1.0,
  title: "Dieselbe Nachricht bei mehreren Quellen",
  link: "https://example.com/thema",
  summary: "kurz",
  image: null,
  published: new Date().toISOString(),
  category: "Politik",
  score: 90,
  coverage: {
    sourceCount: 3,
    others: [
      { source: "spiegel", title: "Anderer Blickwinkel", link: "https://example.com/spiegel" },
      { source: "heise", title: "Dritter Titel", link: 'javascript:alert("coverage")' },
    ],
  },
};

// Artikel ohne Pflichtfelder – darf nicht "undefined" anzeigen oder crashen.
const sparseArticle = {
  id: "sparse-1",
  source: "spiegel",
  sourceWeight: 0.95,
  title: "Artikel ohne Kategorie und Datum",
  link: "https://example.com/sparse",
  published: null,
};

/* ---------- Test-Harness ---------- */

function createWindow({
  store = new Map(),
  payloads,
  captureObservers = false,
  systemDark = false,
  wide = false,
} = {}) {
  const dom = new JSDOM(html, {
    url: "https://example.github.io/news-aggregator/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  // jsdom meldet immer "kein Dark Mode"; fuer den System-Pfad brauchen wir
  // beide Antworten und einen ausloesbaren change-Listener. "wide" steuert die
  // Breitenabfrage, an der die Theme-Vorgabe haengt.
  const mediaListeners = [];
  window.matchMedia = (query) => ({
    media: query,
    matches: query.includes("dark") ? systemDark : query.includes("min-width") ? wide : false,
    addEventListener: (_type, handler) => mediaListeners.push(handler),
    removeEventListener: () => {},
    addListener: (handler) => mediaListeners.push(handler),
    removeListener: () => {},
  });

  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
    },
  });

  window.fetch = async (url) => {
    const key = Object.keys(payloads).find((name) => String(url).includes(name));
    if (key === undefined) return { ok: false, status: 404, json: async () => null };
    const payload = payloads[key];
    if (payload === null) return { ok: false, status: 404, json: async () => null };
    return { ok: true, status: 200, json: async () => payload };
  };

  const observed = [];
  window.IntersectionObserver = class {
    constructor(callback) {
      this.callback = callback;
      this.disconnected = false;
    }
    observe(element) {
      if (captureObservers) observed.push({ observer: this, element });
    }
    unobserve() {}
    disconnect() {
      this.disconnected = true;
    }
  };

  return { window, observed, store, mediaListeners };
}

const defaultPayloads = () => ({
  "data/news.json": {
    ...news,
    articles: [...news.articles, hostile, hostileWithValidLink, longArticle, sparseArticle],
  },
  "data/top-news.json": { ...top, articles: [coveredArticle, ...top.articles] },
  "data/ai-summary.json": {
    generatedAt: new Date().toISOString(),
    summary: "Erste Zeile\n\nZweite <b>Zeile</b> mit Markup",
  },
});

async function boot(options = {}) {
  const context = createWindow({ payloads: defaultPayloads(), ...options });
  context.window.eval(appJs);
  await new Promise((resolve) => context.window.setTimeout(resolve, 60));
  context.doc = context.window.document;
  return context;
}

function items(doc) {
  return [...doc.querySelectorAll("#news-list .news-item")];
}
function clickTab(doc, name) {
  fire(doc.querySelector(`.tab[data-tab="${name}"]`), "click");
}
function fire(element, type) {
  const view = element.ownerDocument.defaultView;
  const event =
    type === "click" ? new view.MouseEvent("click", { bubbles: true }) : new view.Event(type, { bubbles: true });
  element.dispatchEvent(event);
}
// jsdom kennt TouchEvent und Touch nicht, die Handler lesen aber nur
// clientX/clientY aus touches/changedTouches – ein schlichter Event mit
// angehängten Koordinaten reicht als Wischgeste.
function fireTouch(element, type, x, y) {
  const view = element.ownerDocument.defaultView;
  const event = new view.Event(type, { bubbles: true, cancelable: true });
  const point = { clientX: x, clientY: y };
  event.touches = type === "touchend" ? [] : [point];
  event.changedTouches = [point];
  element.dispatchEvent(event);
  return event;
}
function marks(store, key) {
  const raw = store.get(key);
  return raw ? JSON.parse(raw) : {};
}

/* ---------- 1. Laden und Rendern ---------- */

section("Laden und Rendern");
{
  const { doc } = await boot();
  check("Start ist der Top-News-Tab", doc.querySelector(".tab.active").dataset.tab === "top");
  check(
    "Liste und Steuerleiste sichtbar",
    !doc.getElementById("news-list").classList.contains("hidden") &&
      !doc.getElementById("controls").classList.contains("hidden"),
    `${doc.getElementById("news-list").className} / ${doc.getElementById("controls").className}`
  );
  check("Top-News starten direkt mit Artikeln", items(doc).length > 0, `${items(doc).length}`);
  check(
    "Keine Zusammenfassung vor der Liste",
    doc.getElementById("panel-overview").classList.contains("hidden"),
    doc.getElementById("panel-overview").className
  );
  const sourceOptions = [...doc.querySelectorAll("#source-filter option")];
  const expectedSources = new Set(defaultPayloads()["data/news.json"].articles.map((article) => article.source));
  check(
    "Quellenfilter gefüllt",
    sourceOptions.length === expectedSources.size + 1 &&
      sourceOptions.slice(1).every((option) => expectedSources.has(option.value)),
    sourceOptions.map((option) => option.value).join(", ")
  );
  check("Kategoriefilter gefüllt", doc.querySelectorAll("#category-filter option").length > 1);
  check(
    "Aktualisierungszeit gesetzt",
    /^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/.test(doc.getElementById("last-updated").textContent),
    doc.getElementById("last-updated").textContent
  );

  // Der Überblick ist nicht mehr der Start-Tab: für die Zusammenfassung muss
  // er ausdrücklich geöffnet werden.
  clickTab(doc, "overview");
  check(
    "Überblick-Panel sichtbar",
    !doc.getElementById("panel-overview").classList.contains("hidden"),
    doc.getElementById("panel-overview").className
  );
  check(
    "Liste und Steuerleiste versteckt",
    doc.getElementById("news-list").classList.contains("hidden") &&
      doc.getElementById("controls").classList.contains("hidden"),
    `${doc.getElementById("news-list").className} / ${doc.getElementById("controls").className}`
  );
  check("Zusammenfassung sichtbar", !doc.getElementById("ai-summary").classList.contains("hidden"));
  check(
    "Markup in der Zusammenfassung bleibt Text",
    doc.getElementById("ai-summary-content").querySelectorAll("b").length === 0 &&
      doc.getElementById("ai-summary-content").textContent.includes("<b>Zeile</b>")
  );
  check(
    "Stand der Zusammenfassung ist ein lokaler Zeitpunkt",
    /^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/.test(doc.getElementById("ai-summary-time").textContent),
    doc.getElementById("ai-summary-time").textContent
  );
  check(
    "Zeitpunkt ist maschinenlesbar",
    !!doc.getElementById("ai-summary-time").getAttribute("datetime")
  );
  check("Keine Leerstelle bei frischen Daten", doc.getElementById("overview-empty").classList.contains("hidden"));

  clickTab(doc, "latest");
  const sparse = doc.querySelector('[data-id="sparse-1"]');
  check("Fehlende Kategorie erzeugt kein Feld", !sparse.querySelector(".news-category"));
  check("Fehlendes Datum zeigt 'unbekannt'", sparse.querySelector(".news-time").textContent === "unbekannt");
  check("Nirgends 'undefined' im Text", !sparse.textContent.includes("undefined"), sparse.textContent);
}

/* ---------- 2. Escaping und URL-Härtung ---------- */

section("Escaping und URL-Härtung");
{
  const { doc } = await boot();
  clickTab(doc, "latest");
  const item = doc.querySelector('[data-id="hostile-1"]');

  check("Artikel gerendert", !!item);
  check("Titel als Text, nicht als Markup", item.querySelector(".news-title").children.length === 0);
  check(
    "Titelinhalt vollständig erhalten",
    item.querySelector(".news-title").textContent.includes("onerror=alert(1)")
  );
  check("javascript:-Link nicht verlinkt", !item.querySelector(".news-title a"));
  check(
    "Ungültige Bild-URL verworfen",
    !item.querySelector("img") && !!item.querySelector(".news-image-placeholder")
  );

  // Attributnamen prüfen, nicht den HTML-String: ein kodierter Wert innerhalb
  // von src="…" ist harmlos, ein echtes on*-Attribut nicht.
  const attributes = [item, ...item.querySelectorAll("*")].flatMap((el) =>
    [...el.attributes].map((attr) => attr.name.toLowerCase())
  );
  check(
    "Kein on*-Eventhandler im DOM",
    !attributes.some((name) => name.startsWith("on")),
    attributes.filter((name) => name.startsWith("on")).join(", ")
  );
  check("Kein autofocus-Ausbruch", !attributes.includes("autofocus"));

  const valid = doc.querySelector('[data-id="hostile-2"]');
  check("Gültiger https-Link wird verlinkt", !!valid.querySelector(".news-title a"));
  check(
    "Link mit rel=noopener noreferrer",
    valid.querySelector(".news-title a").getAttribute("rel") === "noopener noreferrer"
  );
  check("Link öffnet in neuem Tab", valid.querySelector(".news-title a").target === "_blank");
}

/* ---------- 3. Lesestatus-Umschalter ---------- */

section("Lesestatus-Umschalter");
{
  const [first, second] = news.articles;
  const store = new Map([
    ["news-aggregator-read", JSON.stringify({ [first.id]: Date.now() })],
    ["news-aggregator-scrolled", JSON.stringify({ [second.id]: Date.now() })],
  ]);
  const { doc, window } = await boot({ store });

  const errors = [];
  window.addEventListener("error", (event) => errors.push(event.message));

  check("Kein Gelesen-Tab mehr in der Leiste", !doc.querySelector('.tab[data-tab="read"]'));
  check("Genau drei Haupttabs", doc.querySelectorAll(".tab").length === 3);
  check(
    "Tabs heißen Überblick, Top-News, Neueste",
    [...doc.querySelectorAll(".tab")].map((t) => t.textContent).join("|") === "Überblick|Top-News|Neueste"
  );

  const statusButtons = [...doc.querySelectorAll(".status-btn")];
  check(
    "Drei Statussegmente mit eindeutigen Namen",
    statusButtons.map((b) => b.textContent).join("|") === "Alle|Ungelesen|Gelesen"
  );
  check(
    "Radiogruppe zugänglich aufgebaut",
    doc.querySelector('.status-toggle[role="radiogroup"]') !== null &&
      statusButtons.every((b) => b.getAttribute("role") === "radio")
  );
  check(
    "Start ist Alle",
    doc.querySelector('.status-btn[data-status="all"]').getAttribute("aria-checked") === "true"
  );

  clickTab(doc, "latest");
  const setStatus = (name) => fire(doc.querySelector(`.status-btn[data-status="${name}"]`), "click");

  setStatus("read");
  const list = items(doc);
  check("Rendert ohne Fehler", errors.length === 0, errors.join("; "));
  check("Gelesen zeigt gelesene und überscrollte Artikel", list.length === 2, `${list.length}`);
  check(
    "Nur markierte Artikel",
    list.every((el) => [first.id, second.id].includes(el.dataset.id))
  );
  check("Alle ausgegraut", list.every((el) => el.classList.contains("read")));
  check(
    "aria-checked folgt der Wahl",
    doc.querySelector('.status-btn[data-status="read"]').getAttribute("aria-checked") === "true" &&
      doc.querySelector('.status-btn[data-status="all"]').getAttribute("aria-checked") === "false"
  );
  check(
    "Tab bleibt Neueste, kein aria-Bruch",
    doc.querySelector('.tab[data-tab="latest"]').getAttribute("aria-selected") === "true" &&
      doc.getElementById("news-list").getAttribute("aria-labelledby") === "tab-latest"
  );

  // Ungelesen blendet nur aus, was beim Laden schon gelesen war.
  setStatus("unread");
  const unreadList = items(doc);
  check(
    "Ungelesen ohne die beiden bekannten Artikel",
    unreadList.length > 0 && !unreadList.some((el) => [first.id, second.id].includes(el.dataset.id)),
    `${unreadList.length}`
  );

  setStatus("all");
  check("Zurück in der vollen Liste", items(doc).length > unreadList.length, `${items(doc).length}`);

  // Tabwechsel setzt den Filter zurück: er klebt nicht.
  setStatus("read");
  check("Wieder nur Gelesene", items(doc).length === 2, `${items(doc).length}`);
  clickTab(doc, "top");
  check(
    "Nach Tabwechsel wieder Alle",
    doc.querySelector('.status-btn[data-status="all"]').getAttribute("aria-checked") === "true"
  );
  check("Volle Top-Liste", items(doc).length > 2, `${items(doc).length}`);

  // Leere Statusansichten bekommen eigene, ruhige Hinweise.
  const fresh = await boot();
  clickTab(fresh.doc, "latest");
  fire(fresh.doc.querySelector('.status-btn[data-status="read"]'), "click");
  check(
    "Leere Gelesen-Ansicht mit Hinweis",
    fresh.doc.querySelector("#news-list .empty")?.textContent.includes("gelesenen"),
    fresh.doc.querySelector("#news-list .empty")?.textContent
  );

  // Sind alle Artikel des Tabs beim Laden schon gelesen, bleibt "Ungelesen"
  // leer – mit eigenem Hinweis statt der allgemeinen Leermeldung.
  const allIds = Object.fromEntries(
    [...news.articles, hostile, hostileWithValidLink, longArticle, sparseArticle].map((a) => [a.id, Date.now()])
  );
  const allRead = await boot({ store: new Map([["news-aggregator-read", JSON.stringify(allIds)]]) });
  clickTab(allRead.doc, "latest");
  fire(allRead.doc.querySelector('.status-btn[data-status="unread"]'), "click");
  check(
    "Ungelesen komplett leer: eigener Hinweis",
    allRead.doc.querySelector("#news-list .empty")?.textContent.includes("Alles gelesen"),
    allRead.doc.querySelector("#news-list .empty")?.textContent
  );
}

/* ---------- 4. Ausblenden erst beim nächsten Laden ---------- */

section("Ausblenden erst beim nächsten Laden");
{
  const alreadyRead = news.articles[0].id;
  const store = new Map([["news-aggregator-read", JSON.stringify({ [alreadyRead]: Date.now() })]]);
  const { doc, window, observed } = await boot({ store, captureObservers: true });
  clickTab(doc, "latest");

  // "Alle" filtert nichts: das vorher Gelesene steht markiert in der Liste.
  check("Alle zeigt auch Gelesenes", !!doc.querySelector(`[data-id="${alreadyRead}"]`));

  // "Ungelesen" blendet es aus – die frühere Voreinstellung als eigener
  // Zustand des Dreifach-Umschalters.
  fire(doc.querySelector('.status-btn[data-status="unread"]'), "click");
  check("Vorher Gelesenes ist ausgeblendet", !doc.querySelector(`[data-id="${alreadyRead}"]`));

  const before = items(doc).length;
  const target = items(doc)[0];
  const targetId = target.dataset.id;
  const targetEntry = observed.find((o) => o.element === target);
  targetEntry.observer.callback([{ isIntersecting: true, boundingClientRect: { top: 100 } }]);
  targetEntry.observer.callback([{ isIntersecting: false, boundingClientRect: { top: -50 } }]);

  check("Bleibt nach dem Überscrollen sichtbar", !!doc.querySelector(`[data-id="${targetId}"]`));
  check("Ist ausgegraut", target.classList.contains("read"));

  check("Im Speicher vermerkt", marks(store, "news-aggregator-scrolled")[targetId] > 0);
  check("Gleiches DOM-Element (kein Neuaufbau)", doc.querySelector(`[data-id="${targetId}"]`) === target);

  // Auch ein erzwungener Re-Render darf ihn nicht entfernen: Filter weg und
  // wieder an, der während der Sitzung Gelesene bleibt in "Ungelesen" stehen.
  fire(doc.querySelector('.status-btn[data-status="all"]'), "click");
  fire(doc.querySelector('.status-btn[data-status="unread"]'), "click");
  check("Nach Re-Render weiterhin sichtbar", !!doc.querySelector(`[data-id="${targetId}"]`));
  check("Listenlänge unverändert", items(doc).length === before, `${before} -> ${items(doc).length}`);

  // Überscrollen: ausgrauen, aber nicht entfernen und nicht neu rendern.
  const scrolled = items(doc).find((el) => !el.classList.contains("read"));
  const scrolledId = scrolled.dataset.id;
  const entry = observed.find((o) => o.element === scrolled);
  check("Observer für ungelesenen Artikel vorhanden", !!entry);
  entry.observer.callback([{ isIntersecting: true, boundingClientRect: { top: 100 } }]);
  entry.observer.callback([{ isIntersecting: false, boundingClientRect: { top: -50 } }]);

  check("Überscrollt: ausgegraut", scrolled.classList.contains("read"));
  check("Überscrollt: bleibt in der Liste", !!doc.querySelector(`[data-id="${scrolledId}"]`));
  check("Überscrollt: gleiches DOM-Element", doc.querySelector(`[data-id="${scrolledId}"]`) === scrolled);
  check("Überscrollt: Listenlänge unverändert", items(doc).length === before);
  check("Überscrollt: gespeichert", marks(store, "news-aggregator-scrolled")[scrolledId] > 0);
  check("Observer nach Treffer beendet", entry.observer.disconnected === true);

  // Erst der nächste Ladevorgang blendet aus.
  const reloaded = await boot({ store: new Map(store) });
  clickTab(reloaded.doc, "latest");
  fire(reloaded.doc.querySelector('.status-btn[data-status="unread"]'), "click");
  check("Nach Neuladen ausgeblendet", !reloaded.doc.querySelector(`[data-id="${targetId}"]`));
  check("Auch das Überscrollte ist weg", !reloaded.doc.querySelector(`[data-id="${scrolledId}"]`));
  check("Insgesamt weniger Artikel", items(reloaded.doc).length < before);
  void window;
}

/* ---------- 5. Überscrollen markiert als gelesen ---------- */

section("Überscrollen markiert als gelesen");
{
  const { doc, observed } = await boot({ captureObservers: true });
  clickTab(doc, "latest");
  const target = items(doc)[0];
  const id = target.dataset.id;

  check("Startet ungelesen", !target.classList.contains("read"));

  const entry = observed.find((o) => o.element === target);
  entry.observer.callback([{ isIntersecting: true, boundingClientRect: { top: 100 } }]);
  entry.observer.callback([{ isIntersecting: false, boundingClientRect: { top: -50 } }]);
  check("Überscrollen markiert ihn", target.classList.contains("read"));
  void id;
}

/* ---------- 6. Filter, Suche, Ansicht ---------- */

section("Filter, Suche, Ansicht");
{
  const { doc, window } = await boot();
  clickTab(doc, "latest");
  const all = items(doc).length;

  const sourceFilter = doc.getElementById("source-filter");
  sourceFilter.value = "golem";
  fire(sourceFilter, "change");
  const filtered = items(doc);
  check("Quellenfilter greift", filtered.length > 0 && filtered.length < all);
  check(
    "Nur die gewählte Quelle",
    filtered.every((el) => el.querySelector(".news-source").textContent === "golem")
  );
  check("data-source für die CSS-Farbe gesetzt", filtered[0].querySelector(".news-source").dataset.source === "golem");
  check("Badge zeigt 1", doc.getElementById("filter-badge").textContent === "1");
  check("Badge sichtbar", !doc.getElementById("filter-badge").classList.contains("hidden"));

  sourceFilter.value = "";
  fire(sourceFilter, "change");
  check("Badge wieder versteckt", doc.getElementById("filter-badge").classList.contains("hidden"));
  check("Alle Artikel zurück", items(doc).length === all);

  const search = doc.getElementById("search");
  search.value = "zzzznichtvorhandenzzzz";
  fire(search, "input");
  await new Promise((resolve) => window.setTimeout(resolve, 250));
  check("Suche ohne Treffer zeigt Hinweis", !!doc.querySelector("#news-list .empty"));

  search.value = "";
  fire(search, "input");
  await new Promise((resolve) => window.setTimeout(resolve, 250));
  check("Suche zurückgesetzt", items(doc).length === all);

  fire(doc.querySelector('.view-btn[data-view="list"]'), "click");
  check("Listenansicht aktiv", doc.getElementById("news-list").className.includes("list-view"));
  check(
    "aria-pressed umgeschaltet",
    doc.querySelector('.view-btn[data-view="list"]').getAttribute("aria-pressed") === "true" &&
      doc.querySelector('.view-btn[data-view="grid"]').getAttribute("aria-pressed") === "false"
  );

  fire(doc.getElementById("filter-toggle"), "click");
  check("Filterpanel geöffnet", doc.getElementById("filters-panel").classList.contains("open"));
  check("aria-expanded true", doc.getElementById("filter-toggle").getAttribute("aria-expanded") === "true");
}

/* ---------- 7. Top-Tab und Zusammenfassung ---------- */

section("Top-Tab und Zusammenfassung");
{
  const { doc } = await boot();
  clickTab(doc, "top");
  const heats = [...doc.querySelectorAll(".news-heat")];
  check("Relevanzbalken in jedem Top-Artikel", heats.length === items(doc).length);
  check(
    "Balken trägt --heat zwischen 0 und 1",
    heats.every((h) => {
      const value = Number(h.style.getPropertyValue("--heat"));
      return value > 0 && value <= 1;
    })
  );
  check("Stärkster Artikel hat --heat 1", heats.some((h) => Number(h.style.getPropertyValue("--heat")) === 1));
  check("Rohwert bleibt im title", /Relevanz-Score \d/.test(heats[0].title), heats[0].title);

  clickTab(doc, "latest");
  check("Kein Relevanzbalken im Neueste-Tab", doc.querySelectorAll(".news-heat").length === 0);
  check(
    "Zusammenfassung bleibt dem Überblick vorbehalten",
    doc.getElementById("panel-overview").classList.contains("hidden")
  );
  clickTab(doc, "overview");
  check(
    "Überblick zeigt sie wieder",
    !doc.getElementById("ai-summary").classList.contains("hidden") &&
      doc.getElementById("news-list").classList.contains("hidden"),
    `ai: ${doc.getElementById("ai-summary").className} | list: ${doc.getElementById("news-list").className}`
  );
  clickTab(doc, "latest");

  const toggle = doc.querySelector('[data-id="long-1"] .toggle-summary');
  check("Aufklapp-Button vorhanden", !!toggle);
  const paragraph = toggle.closest(".news-summary");
  check("Vorschau gekürzt", paragraph.querySelector(".summary-preview").textContent.endsWith("…"));
  check("Volltext zunächst versteckt", paragraph.querySelector(".summary-full").classList.contains("hidden"));

  fire(toggle, "click");
  check("Volltext sichtbar", !paragraph.querySelector(".summary-full").classList.contains("hidden"));
  check("Vorschau versteckt", paragraph.querySelector(".summary-preview").classList.contains("hidden"));
  check("Buttontext geändert", toggle.textContent === "Weniger anzeigen");
  check("aria-expanded true", toggle.getAttribute("aria-expanded") === "true");

  fire(toggle, "click");
  check("Wieder zugeklappt", toggle.textContent === "Mehr anzeigen");
  check("aria-expanded false", toggle.getAttribute("aria-expanded") === "false");
}

/* ---------- 7b. Zusammenfassung: Markdown-Light und Sicherheit ---------- */

section("Zusammenfassung: Markdown-Light und Sicherheit");
{
  // Der Überblick ist nicht mehr der Start-Tab; die Zusammenfassung wird erst
  // beim Öffnen gerendert, also gehört der Tabwechsel zum Aufbau.
  const withSummary = async (summary) => {
    const context = await boot({
      payloads: {
        ...defaultPayloads(),
        "data/ai-summary.json": { generatedAt: new Date().toISOString(), summary },
      },
    });
    clickTab(context.doc, "overview");
    return context;
  };
  const content = (doc) => doc.getElementById("ai-summary-content");

  // Typisches Gemini-Format: Überschrift, dann Punkte mit Fettdruck.
  const structured = await withSummary(
    "# Nachrichten des Tages\n* **Koalition:** Einigung im Streit.\n* **Börse:** Dax gibt nach.\n- **Sport:** Titelverteidiger weiter."
  );
  const c1 = content(structured.doc);
  check("Überschrift als h3", c1.querySelector("h3.ai-summary-heading")?.textContent === "Nachrichten des Tages");
  check("Liste mit drei Punkten", c1.querySelectorAll("ul.ai-summary-list li").length === 3);
  check(
    "Fettdruck als strong-Element, nicht als Textsternchen",
    c1.querySelector("li strong")?.textContent === "Koalition:" &&
      !c1.textContent.includes("**Koalition")
  );
  check("Auch '- '-Zeilen werden Listenpunkte", c1.textContent.includes("Titelverteidiger weiter"));

  // Gespeicherter Stand ohne Zeilenumbrüche zwischen den Punkten.
  const glued = await withSummary("* **Erstes:** Text eins. * **Zweites:** Text zwei.");
  const c2 = content(glued.doc);
  check(
    "Verklebte Punkte werden getrennt",
    c2.querySelectorAll("ul.ai-summary-list li").length === 2,
    c2.textContent
  );
  check("Beide Titel als strong", [...c2.querySelectorAll("li strong")].map((s) => s.textContent).join(",") === "Erstes:,Zweites:");

  // Unformatierte Zusammenfassung bleibt Absatztext – keine leeren Listen.
  const plain = await withSummary("Ein ruhiger Absatz ohne Markup.\n\nEin zweiter Absatz.");
  const c3 = content(plain.doc);
  check(
    "Unformatierter Text bleibt Absätze",
    c3.querySelectorAll("p").length === 2 && c3.querySelectorAll("ul, h3").length === 0
  );

  // Kein Satzende nötig: eine volle Einleitungszeile wird nicht zur
  // Überschrift, nur weil danach eine Liste kommt.
  const intro = await withSummary("Heute war viel los. Hier die wichtigsten Punkte:\n* **Thema:** Text.");
  const c4 = content(intro.doc);
  check(
    "Einleitungssatz bleibt Absatz, nicht Überschrift",
    c4.querySelector("h3") === null && c4.querySelector("p")?.textContent.includes("Heute war viel los")
  );

  // Feindseliger Inhalt: bleibt sichtbarer Text, erzeugt keinerlei Markup.
  const hostileSummary = await withSummary(
    '* <img src=x onerror=alert(1)> **Titel" onmouseover="alert(2):** Text <script>alert(3)</script>'
  );
  const c5 = content(hostileSummary.doc);
  check("Kein img aus KI-Inhalt", c5.querySelectorAll("img").length === 0);
  check("Kein script aus KI-Inhalt", c5.querySelectorAll("script").length === 0);
  check("Keine Event-Handler-Attribute im Inhalt",
    ![...c5.querySelectorAll("*")].some((node) =>
      [...node.attributes].some((attr) => attr.name.toLowerCase().startsWith("on"))
    )
  );
  check(
    "Feindseliges Markup bleibt als Text sichtbar",
    c5.textContent.includes("<img src=x") && c5.textContent.includes("<script>")
  );
  check(
    "Anführungszeichen brechen kein Attribut auf",
    c5.querySelector("li strong") !== null && !c5.querySelector("[onmouseover]")
  );

  // Ungerade ** bleiben sichtbarer Text statt halbfetter Ausreißer.
  const odd = await withSummary("* **Kaputt: Text ohne Ende");
  check(
    "Ungerade Fett-Markierung bleibt Text",
    content(odd.doc).querySelectorAll("strong").length === 0 &&
      content(odd.doc).textContent.includes("**Kaputt")
  );
}

/* ---------- 8. Speicherformat: Migration und Verfall ---------- */

section("Speicherformat: Migration und Verfall");
{
  const id = news.articles[0].id;

  const legacyStore = new Map([["news-aggregator-read", JSON.stringify([id])]]);
  const legacy = await boot({ store: legacyStore });
  clickTab(legacy.doc, "latest");
  fire(legacy.doc.querySelector('.status-btn[data-status="unread"]'), "click");
  check("Altes Array-Format wird gelesen", !legacy.doc.querySelector(`[data-id="${id}"]`));
  const migrated = marks(legacyStore, "news-aggregator-read");
  check("Auf Objektformat umgeschrieben", !Array.isArray(migrated) && typeof migrated[id] === "number");

  const oldStore = new Map([
    ["news-aggregator-read", JSON.stringify({ [id]: Date.now() - 60 * 24 * 3600 * 1000 })],
  ]);
  const expired = await boot({ store: oldStore });
  clickTab(expired.doc, "latest");
  fire(expired.doc.querySelector('.status-btn[data-status="unread"]'), "click");
  check("Eintrag älter als 30 Tage verfällt", Object.keys(marks(oldStore, "news-aggregator-read")).length === 0);
  check("Artikel wieder sichtbar", !!expired.doc.querySelector(`[data-id="${id}"]`));

  const recentStore = new Map([
    ["news-aggregator-read", JSON.stringify({ [id]: Date.now() - 5 * 24 * 3600 * 1000 })],
  ]);
  const kept = await boot({ store: recentStore });
  check("Eintrag innerhalb von 30 Tagen bleibt", Object.keys(marks(recentStore, "news-aggregator-read")).length === 1);
  void kept;

  const broken = await boot({ store: new Map([["news-aggregator-read", "{kein json"]]) });
  clickTab(broken.doc, "top");
  check("Defekter Speicherinhalt crasht nicht", items(broken.doc).length > 0);
}

/* ---------- 9. Fehlerfälle ---------- */

section("Fehlerfälle");
{
  const failing = createWindow({
    payloads: { "data/news.json": null, "data/top-news.json": null, "data/ai-summary.json": null },
  });
  failing.window.eval(appJs);
  await new Promise((resolve) => failing.window.setTimeout(resolve, 60));
  check(
    "HTTP-Fehler zeigt Fehlermeldung im aktiven Panel",
    [...failing.window.document.querySelectorAll("#news-list .empty")].some((p) =>
      p.textContent.includes("Fehler beim Laden")
    ),
    failing.window.document.querySelector("#news-list").textContent
  );

  const withoutSummary = await boot({
    payloads: { ...defaultPayloads(), "data/ai-summary.json": null },
  });
  check(
    "Fehlende Zusammenfassung: Artikel in den anderen Tabs bleiben",
    (clickTab(withoutSummary.doc, "top"), items(withoutSummary.doc).length > 0)
  );
  clickTab(withoutSummary.doc, "overview");
  check(
    "Überblick zeigt ruhige Leerstelle statt kaputtem Tab",
    withoutSummary.doc.getElementById("ai-summary").classList.contains("hidden") &&
      !withoutSummary.doc.getElementById("overview-empty").classList.contains("hidden") &&
      withoutSummary.doc.getElementById("overview-empty").textContent.includes("keine aktuelle Zusammenfassung")
  );

  const stale = await boot({
    payloads: {
      ...defaultPayloads(),
      "data/ai-summary.json": {
        generatedAt: new Date(Date.now() - 48 * 3600 * 1000).toISOString(),
        summary: "Zusammenfassung von vorgestern",
      },
    },
  });
  clickTab(stale.doc, "overview");
  check(
    "Veraltete Zusammenfassung: ebenfalls Leerstelle",
    stale.doc.getElementById("ai-summary").classList.contains("hidden") &&
      !stale.doc.getElementById("overview-empty").classList.contains("hidden")
  );
  check(
    "Veralteter Text wird nicht gezeigt",
    !stale.doc.getElementById("panel-overview").textContent.includes("vorgestern")
  );

  const emptyArticles = await boot({
    payloads: {
      ...defaultPayloads(),
      "data/news.json": { generatedAt: new Date().toISOString(), articles: [] },
      "data/top-news.json": { generatedAt: new Date().toISOString(), articles: [] },
    },
  });
  clickTab(emptyArticles.doc, "top");
  check("Leere Datenlage zeigt Hinweis", !!emptyArticles.doc.querySelector("#news-list .empty"));
}

/* ---------- 10. Darstellung: Themes und Hell/Dunkel ---------- */

section("Darstellung: Themes und Hell/Dunkel");
{
  const store = new Map();
  const { doc } = await boot({ store });
  const root = doc.documentElement;
  const themeColor = () => doc.querySelector('meta[name="theme-color"]').content;
  const fontLinks = () => doc.querySelectorAll('link[href*="Newsreader"]');

  check("Standard-Theme ist app", root.dataset.theme === "app");
  check("Standard-Modus ist hell", root.dataset.mode === "light");
  check(
    "App-Knopf ist aktiv",
    doc.querySelector('[data-theme-choice="app"]').classList.contains("is-active") &&
      doc.querySelector('[data-theme-choice="app"]').getAttribute("aria-pressed") === "true"
  );
  check("theme-color passt zum App-Theme", themeColor() === "#ffffff", themeColor());
  check("Serif-Schrift noch nicht angefordert", fontLinks().length === 0);

  fire(doc.querySelector('[data-theme-choice="editorial"]'), "click");
  check("Theme auf editorial", root.dataset.theme === "editorial");
  check(
    "Zeitungs-Knopf aktiv, App-Knopf nicht",
    doc.querySelector('[data-theme-choice="editorial"]').classList.contains("is-active") &&
      !doc.querySelector('[data-theme-choice="app"]').classList.contains("is-active")
  );
  check("theme-color folgt dem Zeitungs-Theme", themeColor() === "#faf7f2", themeColor());
  check("Serif-Schrift nachgeladen", fontLinks().length === 1);
  check(
    "Theme in den Einstellungen gemerkt",
    JSON.parse(store.get("news-aggregator-prefs")).theme === "editorial"
  );

  fire(doc.querySelector('[data-theme-choice="app"]'), "click");
  fire(doc.querySelector('[data-theme-choice="editorial"]'), "click");
  check("Schrift wird nicht doppelt angefordert", fontLinks().length === 1);

  fire(doc.getElementById("mode-toggle"), "click");
  check("Modus auf dunkel", root.dataset.mode === "dark");
  check("theme-color folgt dem Dunkelmodus", themeColor() === "#17140f", themeColor());
  check("Modus gemerkt", JSON.parse(store.get("news-aggregator-prefs")).mode === "dark");
  check(
    "Knopfbeschriftung wechselt",
    doc.getElementById("mode-toggle").getAttribute("aria-label") === "Hellmodus einschalten"
  );

  fire(doc.getElementById("mode-toggle"), "click");
  check("Zurück auf hell", root.dataset.mode === "light");

  const reloaded = await boot({ store: new Map(store) });
  check("Theme überlebt Neuladen", reloaded.doc.documentElement.dataset.theme === "editorial");
  check(
    "Serif-Schrift direkt geladen",
    reloaded.doc.querySelectorAll('link[href*="Newsreader"]').length === 1
  );
}

/* ---------- 11. Systemeinstellung für den Dunkelmodus ---------- */

section("Systemeinstellung für den Dunkelmodus");
{
  const dark = await boot({ systemDark: true });
  check("Folgt dunkler Systemeinstellung", dark.doc.documentElement.dataset.mode === "dark");
  check(
    "Ohne eigene Wahl wird kein Modus gespeichert",
    !JSON.parse(dark.store.get("news-aggregator-prefs") || "{}").mode
  );

  const live = await boot({ systemDark: false });
  check("Start hell", live.doc.documentElement.dataset.mode === "light");
  live.mediaListeners.forEach((handler) => handler({ matches: true }));
  check("Systemwechsel greift", live.doc.documentElement.dataset.mode === "dark");

  const chosen = await boot({ systemDark: false });
  fire(chosen.doc.getElementById("mode-toggle"), "click");
  check("Eigene Wahl gesetzt", chosen.doc.documentElement.dataset.mode === "dark");
  chosen.mediaListeners.forEach((handler) => handler({ matches: false }));
  check("Systemwechsel wird danach ignoriert", chosen.doc.documentElement.dataset.mode === "dark");
}

/* ---------- 12. Layout-Wahl wird gemerkt ---------- */

section("Layout-Wahl wird gemerkt");
{
  const store = new Map();
  const { doc } = await boot({ store });
  check(
    "Startklasse ist Kachelansicht im sichtbaren Panel",
    doc.getElementById("news-list").className.includes("top-view") &&
      !doc.getElementById("news-list").classList.contains("hidden")
  );

  fire(doc.querySelector('.view-btn[data-view="list"]'), "click");
  check("Auf Liste gewechselt", doc.getElementById("news-list").className.includes("list-view"));
  check("Layout gemerkt", JSON.parse(store.get("news-aggregator-prefs")).view === "list");

  const reloaded = await boot({ store: new Map(store) });
  check(
    "Liste überlebt Neuladen",
    reloaded.doc.getElementById("news-list").className.includes("list-view")
  );
  check(
    "Knopfzustand wiederhergestellt",
    reloaded.doc.querySelector('.view-btn[data-view="list"]').getAttribute("aria-pressed") === "true"
  );
}

/* ---------- 13. Gelesen-Kennzeichnung ---------- */

section("Gelesen-Kennzeichnung");
{
  const { doc } = await boot();
  clickTab(doc, "latest");
  const target = items(doc)[0];

  check("Gelesen-Abzeichen ist immer im DOM", !!target.querySelector(".read-badge"));
  check(
    "Abzeichen trägt Text",
    target.querySelector(".read-badge").textContent.includes("Gelesen")
  );
  check("Kein Gelesen-Button an der Karte", !target.querySelector(".toggle-read"));

  fire(target.querySelector(".news-title a"), "click");
  check("Karte trägt die Klasse read", target.classList.contains("read"));
}

/* ---------- 14. Weitere Quellen zur selben Nachricht ---------- */

section("Weitere Quellen zur selben Nachricht");
{
  const { doc } = await boot();
  clickTab(doc, "top");
  const item = doc.querySelector('[data-id="covered-1"]');
  const row = item.querySelector(".news-coverage");
  const chips = [...row.querySelectorAll(".coverage-source")];

  check("Zeile vorhanden", !!row);
  check("Beschriftung davor", row.querySelector(".coverage-label").textContent === "Auch bei");
  check("Alle weiteren Quellen genannt", chips.map((c) => c.textContent).join(",") === "spiegel,heise");
  check(
    "Quellenfarbe wird geerbt",
    chips.every((c) => c.classList.contains("news-source") && c.dataset.source === c.textContent)
  );
  check("Gültiger Link wird verlinkt", chips[0].getAttribute("href") === "https://example.com/spiegel");
  check("Titel der Fremdquelle im title", chips[0].title === "Anderer Blickwinkel");
  check("Link öffnet sicher", chips[0].rel === "noopener noreferrer" && chips[0].target === "_blank");
  check("javascript:-Link wird verworfen", chips[1].tagName === "SPAN", chips[1].outerHTML);
  check("Kein javascript: im Markup", !row.outerHTML.includes("javascript:"), row.outerHTML);

  const expected = [coveredArticle, ...top.articles].filter((article) => article.coverage).length;
  check(
    "Zeile nur bei mehrfach gemeldeten Nachrichten",
    doc.querySelectorAll(".news-coverage").length === expected,
    `${doc.querySelectorAll(".news-coverage").length} von ${expected}`
  );
}

/* ---------- 15. Theme-Vorgabe je Gerät ---------- */

section("Theme-Vorgabe je Gerät");
{
  const narrow = await boot();
  check("Schmaler Bildschirm startet als App", narrow.doc.documentElement.dataset.theme === "app");
  check(
    "Ohne eigene Wahl wird kein Theme gespeichert",
    !JSON.parse(narrow.store.get("news-aggregator-prefs") || "{}").theme
  );

  const desktop = await boot({ wide: true });
  check(
    "Breiter Bildschirm startet als Zeitung",
    desktop.doc.documentElement.dataset.theme === "editorial"
  );

  const chosen = await boot({
    store: new Map([["news-aggregator-prefs", JSON.stringify({ v: 2, theme: "app" })]]),
    wide: true,
  });
  check("Eigene Wahl schlägt die Gerätevorgabe", chosen.doc.documentElement.dataset.theme === "app");

  // Altstände schrieben das Theme bei jeder Änderung mit; das war keine Wahl.
  const legacy = await boot({
    store: new Map([["news-aggregator-prefs", JSON.stringify({ theme: "app", view: "list" })]]),
    wide: true,
  });
  check(
    "Alter Stand ohne Marke gilt nicht als Wahl",
    legacy.doc.documentElement.dataset.theme === "editorial",
    legacy.doc.documentElement.dataset.theme
  );
  check(
    "Layout aus dem alten Stand bleibt erhalten",
    legacy.doc.getElementById("news-list").className.includes("list-view")
  );

  // Modus oder Layout umstellen darf die Vorgabe nicht festschreiben.
  const kept = await boot({ wide: true });
  fire(kept.doc.getElementById("mode-toggle"), "click");
  check(
    "Vorgabe bleibt Vorgabe",
    !JSON.parse(kept.store.get("news-aggregator-prefs")).theme,
    kept.store.get("news-aggregator-prefs")
  );

  fire(kept.doc.querySelector('[data-theme-choice="editorial"]'), "click");
  const saved = JSON.parse(kept.store.get("news-aggregator-prefs"));
  check("Bestätigte Vorgabe wird gespeichert", saved.theme === "editorial");
  check("Stand trägt die Versionsmarke", saved.v === 2, JSON.stringify(saved));
}

/* ---------- 16. Dichte fuer schmale Displays ---------- */

section("Dichte für schmale Displays");
{
  const store = new Map();
  const { doc } = await boot({ store });
  const list = () => doc.getElementById("news-list").className;

  check("Standard-Dichte ist cards", list().includes("density-cards"), list());
  check(
    "Karten-Knopf ist aktiv",
    doc.querySelector('.density-btn[data-density="cards"]').getAttribute("aria-pressed") === "true"
  );
  check(
    "Layout und Dichte stehen gemeinsam an der Liste",
    list().includes("top-view") && list().includes("density-cards"),
    list()
  );

  fire(doc.querySelector('.density-btn[data-density="compact"]'), "click");
  check("Auf kompakt gewechselt", list().includes("density-compact"), list());
  check("Alte Dichte ist weg", !list().includes("density-cards"), list());
  check("Dichte gemerkt", JSON.parse(store.get("news-aggregator-prefs")).density === "compact");
  check(
    "Knopfzustand umgestellt",
    doc.querySelector('.density-btn[data-density="compact"]').getAttribute("aria-pressed") === "true" &&
      doc.querySelector('.density-btn[data-density="cards"]').getAttribute("aria-pressed") === "false"
  );

  // Dichte und Layout sind unabhängig: beide Wahlen bleiben nebeneinander.
  fire(doc.querySelector('.view-btn[data-view="list"]'), "click");
  const prefs = JSON.parse(store.get("news-aggregator-prefs"));
  check("Beide Wahlen gespeichert", prefs.view === "list" && prefs.density === "compact", JSON.stringify(prefs));

  fire(doc.querySelector('.density-btn[data-density="large"]'), "click");
  check("Auf große Karten gewechselt", list().includes("density-large"), list());

  const reloaded = await boot({ store: new Map(store) });
  check(
    "Dichte überlebt Neuladen",
    reloaded.doc.getElementById("news-list").className.includes("density-large"),
    reloaded.doc.getElementById("news-list").className
  );

  const broken = await boot({
    store: new Map([["news-aggregator-prefs", JSON.stringify({ v: 2, density: "riesig" })]]),
  });
  check(
    "Unbekannte Dichte fällt auf cards zurück",
    broken.doc.getElementById("news-list").className.includes("density-cards")
  );
}

/* ---------- 17. Zeitstempel auf schmalen Displays ---------- */

section("Zeitstempel auf schmalen Displays");
{
  const { doc } = await boot();
  const stamp = doc.getElementById("last-updated");
  check(
    "Zeitstempel gesetzt, ohne Füllwort",
    /^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/.test(stamp.textContent),
    stamp.textContent
  );
  // Genau eine Stelle im Markup: keine gespiegelte Kopie in der Tab-Leiste
  // mehr, die für Hilfstechnik versteckt werden müsste.
  check("Nur ein Element trägt die id", doc.querySelectorAll('[id="last-updated"]').length === 1);
  check("Keine Kopie in der Tab-Leiste", !doc.querySelector(".tabbar .masthead-meta"));
  check("Kein Element verbirgt sich vor Hilfstechnik", !stamp.hasAttribute("aria-hidden"));
  check("Nicht ins Leere versteckt", !stamp.classList.contains("hidden"));

  // Der Zeitstempel lebt im Textblock der Marke, damit er auf dem Telefon
  // bündig unter dem Titel stehen und das Markenzeichen beide Zeilen
  // aufspannen kann.
  const brandText = stamp.closest(".brand-text");
  check(
    "Zeitstempel steht im Textblock der Marke",
    !!brandText && brandText.querySelector(".brand-name") !== null && stamp.parentElement === brandText
  );

  const css = fs.readFileSync(path.join(ROOT, "styles.css"), "utf-8");
  const narrowBlock = css.match(/@media \(max-width: 40rem\) \{[\s\S]*?\n\}/);
  check("Mobil-Block vorhanden", !!narrowBlock);
  check(
    "Umschalter sitzen in der oberen rechten Ecke der Kopfzeile",
    /\[data-theme="app"\] \.appearance \{[^}]*position: absolute;[^}]*top: 0\.45rem;/.test(narrowBlock[0]),
    narrowBlock[0].slice(0, 400)
  );
  check(
    "Umschalter beginnen auf Höhe des Markenzeichens",
    /\[data-theme="app"\] \.masthead-inner \{[^}]*padding: 0\.45rem 7\.2rem 0\.4rem 0\.9rem;/.test(narrowBlock[0]) &&
      /\[data-theme="app"\] \.appearance \{[^}]*top: 0\.45rem;/.test(narrowBlock[0])
  );
  check(
    "Zeitstempel stapelt sich bündig unter den Titel",
    /\[data-theme="app"\] \.brand-text \{[^}]*flex-direction: column;[^}]*align-items: flex-start;/.test(narrowBlock[0]),
    narrowBlock[0].slice(0, 400)
  );
  check(
    "Markenzeichen spannt Titel und Zeitstempel auf",
    /\[data-theme="app"\] \.brand-mark \{[^}]*align-self: stretch;[^}]*height: auto;/.test(narrowBlock[0])
  );
  check(
    "Zeitungs-Kopfzeile behält ihre zentrierte Ordnung",
    /\[data-theme="editorial"\] \.masthead-inner \{\s*flex-direction: column;/.test(narrowBlock[0])
  );
  check("Kein Tab-Leisten-Zeitstempel mehr im Stylesheet", !css.includes("masthead-meta-tabbar"));
}

/* ---------- 18. Wischgeste zwischen den Tabs ---------- */

section("Wischgeste zwischen den Tabs");
{
  const { doc } = await boot();
  const list = doc.getElementById("news-list");
  const swipe = (fromX, toX) => {
    fireTouch(list, "touchstart", fromX, 400);
    fireTouch(list, "touchmove", (fromX + toX) / 2, 402);
    fireTouch(list, "touchend", toX, 405);
  };
  const activeTab = () => doc.querySelector(".tab.active").dataset.tab;

  check("Start im Top-News-Tab", activeTab() === "top");
  swipe(40, 260);
  check("Wischen nach rechts -> Überblick", activeTab() === "overview", activeTab());
  swipe(260, 120);
  check("Wischen nach links -> Top-News", activeTab() === "top", activeTab());
  swipe(260, 120);
  check("Nochmal links -> Neueste", activeTab() === "latest", activeTab());
  swipe(260, 120);
  check("Am letzten Tab endet die Geste", activeTab() === "latest", activeTab());
  swipe(40, 260);
  check("Wischen nach rechts -> Top-News", activeTab() === "top", activeTab());
  swipe(40, 260);
  check("Zurück im Überblick", activeTab() === "overview", activeTab());
  swipe(40, 260);
  check("Am ersten Tab endet die Geste", activeTab() === "overview", activeTab());

  // Unter der Auslöseschwelle passiert nichts.
  swipe(200, 165);
  check("Kurzes Wischen bleibt ohne Wirkung", activeTab() === "overview", activeTab());

  // Überwiegend senkrecht = Scrollen, kein Tabwechsel.
  fireTouch(list, "touchstart", 200, 200);
  fireTouch(list, "touchmove", 230, 420);
  fireTouch(list, "touchend", 230, 600);
  check("Senkrechter Lauf ist Scrollen", activeTab() === "overview", activeTab());

  // Ein erkanntes Wischen darf nicht mitscrollen: preventDefault ab dem
  // Erkennen, nicht schon beim Berühren.
  fireTouch(list, "touchstart", 260, 400);
  fireTouch(list, "touchmove", 255, 402);
  const move = fireTouch(list, "touchmove", 180, 404);
  fireTouch(list, "touchend", 120, 405);
  check("Erkanntes Wischen stoppt das Mitscrollen", move.defaultPrevented);
  check("Wischen danach gewertet", activeTab() === "top", activeTab());

  // Echte Finger zittern: ein waagerechter Wisch, der mit einem senkrechten
  // Zucken beginnt, darf nicht früh als "Scrollen" abgehakt werden. Früher
  // verfiel die Richtungsentscheidung auf den ersten Probe-Move – so fühlte
  // sich eine Seite unzuverlässig an.
  clickTab(doc, "overview");
  fireTouch(list, "touchstart", 260, 400);
  fireTouch(list, "touchmove", 269, 413); // mehr hoch als quer, aber uneindeutig
  fireTouch(list, "touchmove", 200, 420); // korrigiert klar nach links
  fireTouch(list, "touchend", 140, 422);
  check("Senkrechter Fehlstart heilt sich", activeTab() === "top", activeTab());

  // Wieder zurück, diesmal mit Zucken in die andere Startrichtung.
  fireTouch(list, "touchstart", 80, 400);
  fireTouch(list, "touchmove", 89, 412);
  fireTouch(list, "touchmove", 160, 418);
  fireTouch(list, "touchend", 240, 420);
  check("Rechtswisch nach Fehlstart -> Überblick", activeTab() === "overview", activeTab());

  // Wer weit zieht und am Ende leicht zurückfedert, verliert den Wisch
  // nicht: bewertet wird die maximale Reichweite, nicht das Loslassen.
  fireTouch(list, "touchstart", 260, 400);
  fireTouch(list, "touchmove", 110, 402);
  fireTouch(list, "touchend", 155, 402);
  check("Zurückfedern verwirft keinen Wisch", activeTab() === "top", activeTab());

  // Während des Wischens folgt der Inhalt dem Finger und gleitet beim
  // Loslassen sichtbar zurück.
  fireTouch(list, "touchstart", 260, 400);
  const dragging = fireTouch(list, "touchmove", 180, 404);
  const main = doc.querySelector("main");
  check("Inhalt folgt dem Finger", /translateX\(-80px\)/.test(main.style.transform), main.style.transform);
  check("Leicht abgedunkelt", Number(main.style.opacity) < 1 && Number(main.style.opacity) > 0.7, main.style.opacity);
  fireTouch(list, "touchend", 120, 405);
  check("Nach dem Loslassen zurückgesetzt", main.style.transform === "" && main.style.opacity === "");
  check("Rückweg als Transition", main.classList.contains("swipe-settle"));
  check("Tab gewechselt", activeTab() === "latest", activeTab());
  void dragging;

  // An der Kante der Tab-Reihe: gedämpfter Widerstand statt Mitführen,
  // und am Ende sauber aufgeräumt. Im Überblick führt rechts nirgendwohin.
  clickTab(doc, "overview");
  fireTouch(list, "touchstart", 60, 400);
  fireTouch(list, "touchmove", 170, 404); // weiter rechts gibt es keinen Tab
  const edgeTransform = main.style.transform;
  check("Widerstand an der Kante", /translateX\(/.test(edgeTransform), edgeTransform);
  const dragged = Number(edgeTransform.match(/translateX\((-?[\d.]+)px\)/)[1]);
  check("Deutlich kürzer als der Fingerweg", dragged > 0 && dragged < 60, `${dragged}`);
  fireTouch(list, "touchend", 170, 404);
  check("An der Kante kein Tabwechsel", activeTab() === "overview", activeTab());
  check("Nach Kante aufgeräumt", main.style.transform === "");

  // Senkrechtes Scrollen und Gesten auf Links erzeugen nie Feedback.
  fireTouch(list, "touchstart", 200, 200);
  fireTouch(list, "touchmove", 230, 420);
  fireTouch(list, "touchend", 230, 600);
  check("Scrollen erzeugt kein Feedback", main.style.transform === "" && !main.classList.contains("swipe-settle"));

  // Links und Buttons behalten ihre eigene Geste.
  clickTab(doc, "top");
  const link = list.querySelector(".news-title a");
  if (link) {
    fireTouch(link, "touchstart", 260, 400);
    fireTouch(link, "touchmove", 180, 404);
    fireTouch(link, "touchend", 120, 405);
    check("Wischen auf einem Link bleibt ohne Wirkung", activeTab() === "top", activeTab());
    check("Auch ohne Feedback", doc.querySelector("main").style.transform === "");
  } else {
    check("Wischen auf einem Link bleibt ohne Wirkung", false, "kein Link in der Liste");
  }

  // Auch im Überblick funktioniert die Geste, obwohl dort keine Liste steht:
  // die Handler hängen an main.
  clickTab(doc, "overview");
  fireTouch(doc.getElementById("panel-overview"), "touchstart", 260, 400);
  fireTouch(doc.getElementById("panel-overview"), "touchmove", 180, 404);
  fireTouch(doc.getElementById("panel-overview"), "touchend", 120, 405);
  check("Wischen im Überblick wechselt den Tab", activeTab() === "top", activeTab());

  // Ein Tabwechsel per Geste rendert wie ein Klick: Liste und Steuerleiste
  // folgen dem gewählten Panel.
  swipe(260, 120);
  check("Nach Wischen auf Neueste ist das Panel richtig", activeTab() === "latest", activeTab());
  check(
    "Überblick-Panel dabei versteckt",
    doc.getElementById("panel-overview").classList.contains("hidden") &&
      !doc.getElementById("controls").classList.contains("hidden")
  );
  check(
    "Panel verweist auf den gewischten Tab",
    list.getAttribute("aria-labelledby") === "tab-latest"
  );
  swipe(40, 260);
  swipe(40, 260);
  check("Zurückgewischt in den Überblick", activeTab() === "overview", activeTab());
  check(
    "Zusammenfassung wieder sichtbar",
    !doc.getElementById("ai-summary").classList.contains("hidden") &&
      doc.getElementById("news-list").classList.contains("hidden"),
    `ai: ${doc.getElementById("ai-summary").className} | list: ${doc.getElementById("news-list").className}`
  );
}

console.log(`\n${failures === 0 ? "Alle Tests bestanden." : `${failures} Test(s) fehlgeschlagen.`}`);
process.exit(failures === 0 ? 0 : 1);
