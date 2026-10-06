// App-shell cache only. Weather/API responses are NEVER served stale as if fresh:
// network first; if offline the page shows "no connection" (no fabricated data).
const SHELL = "kanofarm-shell-v19";
const FILES = ["/", "/static/app.js", "/static/queue.js", "/static/style.css", "/manifest.webmanifest", "/api/crop-calendar", "/api/sources"];
self.addEventListener("install", e => e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES))));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  // Only static reference guides are cached under /api/ (calendar, sources). Weather and farm data are never served from this cache.
  const guide = ["/api/crop-calendar", "/api/sources", "/api/states"].includes(url.pathname);
  if (e.request.method !== "GET" || (url.pathname.startsWith("/api/") && !guide)) return;
  e.respondWith(fetch(e.request).then(r => {
    const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request)));
});

// Alerts sent by the server (web push). Only shows text the server composed from the farmer's own advisories.
self.addEventListener("push", e => {
  let d = {}; try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "KanoFarm AI", {
    body: d.body || "", tag: d.tag || "kanofarm-alert", icon: "/static/icons/icon-192.png", data: { url: d.url || "/" } }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(cs => {
    for (const c of cs) if ("focus" in c) { c.navigate(url); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
