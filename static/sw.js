const CACHE_NAME = "auraxl-uptime-v1";
const ASSETS = [
  "/",
  "/manifest.json",
  "/static/dashboard.html",
  "/static/logo.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  // Always fetch fresh API requests over network
  if (e.request.url.includes("/api/") || e.request.url.includes("/health")) {
    return;
  }
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
