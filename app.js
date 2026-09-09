const STORAGE_KEY = "news-aggregator-read";
const SCROLLED_KEY = "news-aggregator-scrolled";

let allArticles = [];
let topArticles = [];
let aiSummary = null;
let currentTab = "top";
let viewMode = "grid"; // 'grid' oder 'list'
const PREVIEW_LENGTH = 280;

// Service Worker registrieren
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(console.error);
}

// Lesestatus aus localStorage laden
function getReadIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function getScrolledIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(SCROLLED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function markAsRead(id) {
  const read = getReadIds();
  read.add(id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...read]));
  render();
}

function markAsUnread(id) {
  const read = getReadIds();
  read.delete(id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...read]));
  render();
}

// Artikel als "überscrollt" merken, wenn er den Viewport nach oben verlässt
function observeScrolled(element, id) {
  if (!("IntersectionObserver" in window)) return;

  let hasBeenVisible = false;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          hasBeenVisible = true;
        } else if (hasBeenVisible && entry.boundingClientRect.top < 0) {
          // Artikel wurde gesehen und ist jetzt über dem sichtbaren Bereich
          const scrolled = getScrolledIds();
          if (!scrolled.has(id)) {
            scrolled.add(id);
            localStorage.setItem(SCROLLED_KEY, JSON.stringify([...scrolled]));
            render();
          }
          observer.unobserve(element);
        }
      });
    },
    { threshold: 0.1 }
  );

  observer.observe(element);
}

// Daten laden
async function loadData() {
  try {
    const [newsRes, topRes, aiRes] = await Promise.all([
      fetch("data/news.json"),
      fetch("data/top-news.json"),
      fetch("data/ai-summary.json"),
    ]);

    const newsData = await newsRes.json();
    const topData = await topRes.json();

    allArticles = newsData.articles || [];
    topArticles = topData.articles || [];

    if (aiRes.ok) {
      aiSummary = await aiRes.json();
    }

    document.getElementById("last-updated").textContent = `Letzte Aktualisierung: ${formatDate(
      newsData.generatedAt
    )}`;

    populateFilters();
    render();
  } catch (error) {
    console.error(error);
    document.getElementById("news-list").innerHTML =
      `<p class="empty">Fehler beim Laden der News. Bitte später erneut versuchen.</p>`;
  }
}

function formatDate(isoString) {
  if (!isoString) return "unbekannt";
  const date = new Date(isoString);
  return date.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timeAgo(isoString) {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 5) return "gerade eben";
  if (diffMins < 60) return `vor ${diffMins} Min`;
  if (diffHours < 24) return `vor ${diffHours} Std`;
  return `vor ${diffDays} Tagen`;
}

function populateFilters() {
  const sourceSelect = document.getElementById("source-filter");
  const categorySelect = document.getElementById("category-filter");

  const sources = [...new Set(allArticles.map((a) => a.source))].sort();
  const categories = [...new Set(allArticles.map((a) => a.category))].sort();

  sourceSelect.innerHTML = '<option value="">Alle Quellen</option>';
  sources.forEach((source) => {
    const option = document.createElement("option");
    option.value = source;
    option.textContent = source;
    sourceSelect.appendChild(option);
  });

  categorySelect.innerHTML = '<option value="">Alle Kategorien</option>';
  categories.forEach((category) => {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    categorySelect.appendChild(option);
  });
}

function getFilteredArticles() {
  const search = document.getElementById("search").value.toLowerCase();
  const source = document.getElementById("source-filter").value;
  const category = document.getElementById("category-filter").value;
  const hideRead = document.getElementById("hide-read").checked;
  const readIds = getReadIds();
  const scrolledIds = getScrolledIds();

  let articles = currentTab === "top" ? topArticles : allArticles;

  if (currentTab === "read") {
    // Gelesen-Tab: nur gelesene/überscrollte Artikel
    articles = allArticles.filter((a) => readIds.has(a.id) || scrolledIds.has(a.id));
    hideRead = false; // im Gelesen-Tab nie ausblenden
  }

  return articles.filter((article) => {
    if (source && article.source !== source) return false;
    if (category && article.category !== category) return false;
    if (hideRead && (readIds.has(article.id) || scrolledIds.has(article.id))) return false;
    if (search) {
      const text = (article.title + " " + article.summary + " " + article.source).toLowerCase();
      if (!text.includes(search)) return false;
    }
    return true;
  });
}

function render() {
  const list = document.getElementById("news-list");
  const articles = getFilteredArticles();
  const readIds = getReadIds();
  const scrolledIds = getScrolledIds();

  // AI-Zusammenfassung nur im Top-Tab anzeigen
  const aiSection = document.getElementById("ai-summary");
  const aiContent = document.getElementById("ai-summary-content");
  if (currentTab === "top" && aiSummary?.summary) {
    aiSection.classList.remove("hidden");
    aiContent.innerHTML = aiSummary.summary
      .split("\n")
      .filter((line) => line.trim())
      .map((line) => `<p>${escapeHtml(line)}</p>`)
      .join("");
  } else {
    aiSection.classList.add("hidden");
  }

  if (articles.length === 0) {
    list.innerHTML = `<p class="empty">Keine News gefunden.</p>`;
    return;
  }

  list.innerHTML = "";
  const isGrid = viewMode === "grid";
  list.className = `news-list ${isGrid ? "top-view" : "list-view"}`;

  articles.forEach((article) => {
    const isRead = readIds.has(article.id) || scrolledIds.has(article.id);
    const item = document.createElement("article");
    item.className = `news-item ${isGrid ? "grid-item" : "list-item"} ${isRead ? "read" : ""}`;
    item.dataset.id = article.id;

    const imageHtml = article.image
      ? `<img class="news-image" src="${escapeHtml(article.image)}" alt="" loading="lazy">`
      : `<div class="news-image-placeholder">📰</div>`;

    const summary = article.summary || "";
    const isLong = summary.length > PREVIEW_LENGTH;
    const previewText = isLong ? summary.slice(0, PREVIEW_LENGTH).trim() + "…" : summary;
    const summaryHtml = isLong
      ? `<p class="news-summary">
           <span class="summary-preview">${escapeHtml(previewText)}</span>
           <span class="summary-full hidden">${escapeHtml(summary)}</span>
           <button class="toggle-summary" data-expanded="false">Mehr anzeigen</button>
         </p>`
      : `<p class="news-summary">${escapeHtml(summary)}</p>`;

    item.innerHTML = `
      ${imageHtml}
      <div class="news-content">
        <div class="news-meta">
          <span class="news-source ${escapeHtml(article.source)}">${escapeHtml(article.source)}</span>
          <span class="news-category">${escapeHtml(article.category)}</span>
          <span class="news-time" title="${formatDate(article.published)}">${timeAgo(
            article.published
          )}</span>
          ${currentTab === "top" ? `<span class="news-score">Score: ${article.score}</span>` : ""}
        </div>
        <h2 class="news-title">
          <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" data-id="${
            article.id
          }">${escapeHtml(article.title)}</a>
        </h2>
        ${summaryHtml}
        <div class="news-actions">
          <button class="toggle-read" data-id="${article.id}">
            ${isRead ? "Als ungelesen markieren" : "Als gelesen markieren"}
          </button>
        </div>
      </div>
    `;

    // Klick auf Link = gelesen markieren
    const link = item.querySelector("a");
    link.addEventListener("click", () => markAsRead(article.id));

    // Toggle-Button
    const toggleBtn = item.querySelector(".toggle-read");
    toggleBtn.addEventListener("click", () => {
      if (isRead) {
        markAsUnread(article.id);
      } else {
        markAsRead(article.id);
      }
    });

    // Zusammenfassung auf-/zuklappen
    const toggleSummaryBtn = item.querySelector(".toggle-summary");
    if (toggleSummaryBtn) {
      toggleSummaryBtn.addEventListener("click", () => {
        const preview = item.querySelector(".summary-preview");
        const full = item.querySelector(".summary-full");
        const expanded = toggleSummaryBtn.dataset.expanded === "true";
        toggleSummaryBtn.dataset.expanded = !expanded;
        preview.classList.toggle("hidden", !expanded);
        full.classList.toggle("hidden", expanded);
        toggleSummaryBtn.textContent = expanded ? "Mehr anzeigen" : "Weniger anzeigen";
      });
    }

    // Überscrollen beobachten
    observeScrolled(item, article.id);

    list.appendChild(item);
  });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Event Listener
function init() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentTab = tab.dataset.tab;
      render();
    });
  });

  ["search", "source-filter", "category-filter", "hide-read"].forEach((id) => {
    document.getElementById(id).addEventListener("input", render);
  });

  const filterToggle = document.getElementById("filter-toggle");
  const filtersPanel = document.getElementById("filters-panel");
  filterToggle.addEventListener("click", () => {
    const open = filtersPanel.classList.toggle("open");
    filterToggle.classList.toggle("active", open);
    filterToggle.setAttribute("aria-expanded", String(open));
  });

  const updateFilterBadge = () => {
    const active = ["source-filter", "category-filter"].filter(
      (id) => document.getElementById(id).value !== ""
    ).length;
    const badge = document.getElementById("filter-badge");
    badge.textContent = active;
    badge.classList.toggle("hidden", active === 0);
  };
  ["source-filter", "category-filter"].forEach((id) => {
    document.getElementById(id).addEventListener("input", updateFilterBadge);
  });
  updateFilterBadge();

  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".view-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      viewMode = btn.dataset.view;
      render();
    });
  });

  loadData();
}

init();
