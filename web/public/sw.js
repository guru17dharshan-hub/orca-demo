// ORCA service worker: installable app + the last answers when the phone loses signal at the jetty.
//  • app shell and pages: network first, saved copy when offline
//  • /assets/* (content-hashed by Vite): saved on first use, never refetched
//  • GET /api/* answers (verdicts, warnings, zones, bulletin): network first, saved copy when offline
//  • never cached: anything that is not GET, the live alert stream, speech and transcription
const VERSION = "orca-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/favicon.svg", "/icon-192.png", "/land-india.png"];
const NO_CACHE = ["/api/alerts/stream", "/api/transcribe", "/api/speak", "/api/subscriptions", "/api/outbox"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(VERSION);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const saved = (await cache.match(request)) || (fallbackUrl && (await cache.match(fallbackUrl)));
    if (saved) return saved;
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(VERSION);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (NO_CACHE.some((p) => url.pathname.startsWith(p))) return;
  if (request.mode === "navigate") return event.respondWith(networkFirst(request, "/index.html"));
  if (url.pathname.startsWith("/assets/")) return event.respondWith(cacheFirst(request));
  event.respondWith(networkFirst(request));
});
