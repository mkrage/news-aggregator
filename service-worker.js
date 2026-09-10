const CACHE_NAME = "news-aggregator-v10";
// Relative Pfade, damit der Worker auch lokal und unter anderem Basispfad
// installiert. cache.addAll() ist atomar – ein 404 verhindert die Installation.
const STATIC_ASSETS = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.json",
  "./favicon.svg",
  "./favicon-32.png",
  "./apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Nur eigene Herkunft bedienen – Bilder der Nachrichtenseiten nicht anfassen.
  if (url.origin !== self.location.origin) return;

  // JSON-Daten nie cachen, immer frisch vom Netzwerk holen.
  if (url.pathname.endsWith(".json")) {
    event.respondWith(fetch(request).catch(() => caches.match(request)));
    return;
  }

  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});
