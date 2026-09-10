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
function marks(store, key) {
  const raw = store.get(key);
  return raw ? JSON.parse(raw) : {};
}

/* ---------- 1. Laden und Rendern ---------- */

section("Laden und Rendern");
{
  const { doc } = await boot();
  check("Artikel gerendert", items(doc).length > 0, `${items(doc).length}`);
  check(
    "Aktualisierungszeit gesetzt",
    /Aktualisiert \d{2}\.\d{2}\.\d{4}/.test(doc.getElementById("last-updated").textContent),
    doc.getElementById("last-updated").textContent
  );
  check("Quellenfilter gefüllt", doc.querySelectorAll("#source-filter option").length === 5);
  check("Kategoriefilter gefüllt", doc.querySelectorAll("#category-filter option").length > 1);
  check("Zusammenfassung sichtbar", !doc.getElementById("ai-summary").classList.contains("hidden"));
  check(
    "Markup in der Zusammenfassung bleibt Text",
    doc.getElementById("ai-summary-content").querySelectorAll("b").length === 0 &&
      doc.getElementById("ai-summary-content").textContent.includes("<b>Zeile</b>")
  );

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

/* ---------- 3. Gelesen-Tab ---------- */

section("Gelesen-Tab");
{
  const [first, second] = news.articles;
  const store = new Map([
    ["news-aggregator-read", JSON.stringify({ [first.id]: Date.now() })],
    ["news-aggregator-scrolled", JSON.stringify({ [second.id]: Date.now() })],
  ]);
  const { doc, window } = await boot({ store });

  const errors = [];
  window.addEventListener("error", (event) => errors.push(event.message));

  clickTab(doc, "read");
  const list = items(doc);
  check("Rendert ohne Fehler", errors.length === 0, errors.join("; "));
  check("Zeigt gelesene und überscrollte Artikel", list.length === 2, `${list.length}`);
  check(
    "Nur markierte Artikel",
    list.every((el) => [first.id, second.id].includes(el.dataset.id))
  );
  check("Alle ausgegraut", list.every((el) => el.classList.contains("read")));
  check(
    "aria-selected gesetzt",
    doc.querySelector('.tab[data-tab="read"]').getAttribute("aria-selected") === "true" &&
      doc.querySelector('.tab[data-tab="top"]').getAttribute("aria-selected") === "false"
  );
  check(
    "Panel verweist auf den aktiven Tab",
    doc.getElementById("news-list").getAttribute("aria-labelledby") === "tab-read"
  );

  fire(list[0].querySelector(".toggle-read"), "click");
  check("'Ungelesen' entfernt den Artikel aus dem Tab", items(doc).length === 1);

  fire(items(doc)[0].querySelector(".toggle-read"), "click");
  check("Leerer Tab zeigt Hinweis", !!doc.querySelector("#news-list .empty"));
}

/* ---------- 4. Ausblenden erst beim nächsten Laden ---------- */

section("Ausblenden erst beim nächsten Laden");
{
  const alreadyRead = news.articles[0].id;
  const store = new Map([["news-aggregator-read", JSON.stringify({ [alreadyRead]: Date.now() })]]);
  const { doc, window, observed } = await boot({ store, captureObservers: true });
  clickTab(doc, "latest");

  check("Vorher Gelesenes ist ausgeblendet", !doc.querySelector(`[data-id="${alreadyRead}"]`));

  const before = items(doc).length;
  const target = items(doc)[0];
  const targetId = target.dataset.id;
  fire(target.querySelector(".toggle-read"), "click");

  check("Bleibt nach dem Markieren sichtbar", !!doc.querySelector(`[data-id="${targetId}"]`));
  check("Ist ausgegraut", target.classList.contains("read"));
  check(
    "Buttontext umgeschaltet",
    target.querySelector(".toggle-read").textContent.trim() === "Als ungelesen markieren"
  );
  check("Im Speicher vermerkt", marks(store, "news-aggregator-read")[targetId] > 0);
  check("Gleiches DOM-Element (kein Neuaufbau)", doc.querySelector(`[data-id="${targetId}"]`) === target);

  // Auch ein erzwungener Re-Render darf ihn nicht entfernen.
  fire(doc.getElementById("hide-read"), "change");
  fire(doc.getElementById("hide-read"), "change");
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
  check("Nach Neuladen ausgeblendet", !reloaded.doc.querySelector(`[data-id="${targetId}"]`));
  check("Auch das Überscrollte ist weg", !reloaded.doc.querySelector(`[data-id="${scrolledId}"]`));
  check("Insgesamt weniger Artikel", items(reloaded.doc).length < before);
  void window;
}

/* ---------- 5. Bewusst ungelesen bleibt ungelesen ---------- */

section("Bewusst ungelesen bleibt ungelesen");
{
  const { doc, observed } = await boot({ captureObservers: true });
  clickTab(doc, "latest");
  const target = items(doc)[0];
  const id = target.dataset.id;

  fire(target.querySelector(".toggle-read"), "click");
  fire(target.querySelector(".toggle-read"), "click");
  check("Wieder ungelesen", !target.classList.contains("read"));

  const entry = observed.find((o) => o.element === target);
  entry.observer.callback([{ isIntersecting: true, boundingClientRect: { top: 100 } }]);
  entry.observer.callback([{ isIntersecting: false, boundingClientRect: { top: -50 } }]);
  check("Scrollen markiert ihn nicht erneut", !target.classList.contains("read"));
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
  check("Zusammenfassung nur im Top-Tab", doc.getElementById("ai-summary").classList.contains("hidden"));

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

/* ---------- 8. Speicherformat: Migration und Verfall ---------- */

section("Speicherformat: Migration und Verfall");
{
  const id = news.articles[0].id;

  const legacyStore = new Map([["news-aggregator-read", JSON.stringify([id])]]);
  const legacy = await boot({ store: legacyStore });
  clickTab(legacy.doc, "latest");
  check("Altes Array-Format wird gelesen", !legacy.doc.querySelector(`[data-id="${id}"]`));
  const migrated = marks(legacyStore, "news-aggregator-read");
  check("Auf Objektformat umgeschrieben", !Array.isArray(migrated) && typeof migrated[id] === "number");

  const oldStore = new Map([
    ["news-aggregator-read", JSON.stringify({ [id]: Date.now() - 60 * 24 * 3600 * 1000 })],
  ]);
  const expired = await boot({ store: oldStore });
  clickTab(expired.doc, "latest");
  check("Eintrag älter als 30 Tage verfällt", Object.keys(marks(oldStore, "news-aggregator-read")).length === 0);
  check("Artikel wieder sichtbar", !!expired.doc.querySelector(`[data-id="${id}"]`));

  const recentStore = new Map([
    ["news-aggregator-read", JSON.stringify({ [id]: Date.now() - 5 * 24 * 3600 * 1000 })],
  ]);
  const kept = await boot({ store: recentStore });
  check("Eintrag innerhalb von 30 Tagen bleibt", Object.keys(marks(recentStore, "news-aggregator-read")).length === 1);
  void kept;

  const broken = await boot({ store: new Map([["news-aggregator-read", "{kein json"]]) });
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
    "HTTP-Fehler zeigt Fehlermeldung",
    failing.window.document.querySelector("#news-list .empty")?.textContent.includes("Fehler beim Laden")
  );

  const withoutSummary = await boot({
    payloads: { ...defaultPayloads(), "data/ai-summary.json": null },
  });
  check(
    "Fehlende Zusammenfassung stört nicht",
    items(withoutSummary.doc).length > 0 &&
      withoutSummary.doc.getElementById("ai-summary").classList.contains("hidden")
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
  check(
    "Veraltete Zusammenfassung wird ignoriert",
    stale.doc.getElementById("ai-summary").classList.contains("hidden")
  );

  const emptyArticles = await boot({
    payloads: {
      ...defaultPayloads(),
      "data/news.json": { generatedAt: new Date().toISOString(), articles: [] },
      "data/top-news.json": { generatedAt: new Date().toISOString(), articles: [] },
    },
  });
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
  check("Start in Kachelansicht", doc.getElementById("news-list").className.includes("top-view"));

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
  check(
    "Knopf zeigt Symbol und Text",
    !!target.querySelector(".toggle-read svg") &&
      target.querySelector(".toggle-read").textContent.includes("Als gelesen markieren")
  );

  fire(target.querySelector(".toggle-read"), "click");
  check("Karte trägt die Klasse read", target.classList.contains("read"));
  check(
    "Knopftext umgestellt",
    target.querySelector(".toggle-read").textContent.trim() === "Als ungelesen markieren"
  );
  check("Kein Symbol mehr im Knopf", !target.querySelector(".toggle-read svg"));
}

/* ---------- 14. Weitere Quellen zur selben Nachricht ---------- */

section("Weitere Quellen zur selben Nachricht");
{
  const { doc } = await boot();
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

console.log(`\n${failures === 0 ? "Alle Tests bestanden." : `${failures} Test(s) fehlgeschlagen.`}`);
process.exit(failures === 0 ? 0 : 1);
