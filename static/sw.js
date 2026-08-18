/**
 * AuraXL Monitor Service Worker
 * Handles PWA caching and BACKGROUND PUSH NOTIFICATIONS
 * — notifications arrive even when the app is closed/tab is shut.
 */

const CACHE_NAME = "auraxl-uptime-v3";
const ASSETS = [
  "/",
  "/manifest.json",
  "/static/logo.png"
];

// ── Install: cache shell assets ──────────────────────────────────────────────
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

// ── Activate: clean old caches ───────────────────────────────────────────────
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch: network-first for API, cache-fallback for assets ─────────────────
self.addEventListener("fetch", (e) => {
  if (e.request.url.includes("/api/") || e.request.url.includes("/health")) {
    return; // Always fresh from network for API
  }
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

// ═══════════════════════════════════════════════════════════════════════════
//  BACKGROUND PUSH NOTIFICATIONS (works when app is closed / phone locked)
// ═══════════════════════════════════════════════════════════════════════════
self.addEventListener("push", (e) => {
  let data = {
    title: "AuraXL Guardian Alert",
    body: "⚠️ Website status has changed!",
    icon: "/static/icons/icon-192x192.png",
    badge: "/static/icons/icon-72x72.png",
    vibrate: [200, 100, 200, 100, 200],
    data: { url: "/" }
  };

  // Parse JSON payload from server
  if (e.data) {
    try {
      const parsed = e.data.json();
      data = { ...data, ...parsed };
    } catch (_) {
      data.body = e.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: data.icon,
    badge: data.badge,
    vibrate: data.vibrate,
    data: data.data,
    requireInteraction: true,   // stays visible until user taps it (like WhatsApp)
    tag: "auraxl-status-alert", // replaces previous alert instead of stacking
    renotify: true              // still vibrates/sounds even if same tag
  };

  e.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// ── Notification click: open/focus the dashboard tab ────────────────────────
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const targetUrl = e.notification.data?.url || "/";
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      // If dashboard is already open, focus it
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          return client.focus();
        }
      }
      // Otherwise open a new tab
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
