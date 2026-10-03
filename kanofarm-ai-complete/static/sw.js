// App-shell cache only. Weather/API responses are NEVER served stale as if fresh:
// network first; if offline the page shows "no connection" (no fabricated data).
const SHELL = "kanofarm-shell-v17";
const FILES = ["/", "/static/app.js", "/static/style.css", "/manifest.webmanifest", "/api/crop-calendar", "/api/sources"];
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
