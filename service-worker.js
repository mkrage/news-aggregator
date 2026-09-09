const CACHE_NAME = "news-aggregator-v2";
const STATIC_ASSETS = [
  "/news-aggregator/",
  "/news-aggregator/index.html",
  "/news-aggregator/styles.css",
  "/news-aggregator/app.js",
  "/news-aggregator/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  // JSON-Daten nie cachen, immer frisch vom Netzwerk holen
  if (request.url.endsWith(".json")) {
    event.respondWith(fetch(request).catch(() => caches.match(request)));
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});
