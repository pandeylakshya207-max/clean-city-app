// Clean City service worker: installs the app, keeps it working without internet,
// and sends reports saved offline as soon as the connection is back.
importScripts("/static/offline.js");

const VERSION = "cc-v4";
const SHELL = VERSION + "-shell";
const PHOTOS = VERSION + "-photos";
const PRECACHE = [
  "/report",
  "/profile",
  "/static/ui.css",
  "/static/ui.js",
  "/static/offline.js",
  "/static/icon-192.png",
  "/manifest.webmanifest",
];
const STATIC_HOSTS = ["cdnjs.cloudflare.com", "fonts.googleapis.com", "fonts.gstatic.com"];
const OFFLINE_PAGE = "<!doctype html><meta name=viewport content='width=device-width'><title>Clean City</title>" +
  "<body style='font-family:system-ui;padding:24px;background:#eef2ea;color:#17211b'><h1>Clean City</h1>" +
  "<p>You are offline and this page has not been opened before. Connect to the internet and try again.</p>";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request) {
  const cache = await caches.open(SHELL);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const hit = await cache.match(request, { ignoreSearch: true });
    if (hit) return hit;
    if (request.mode !== "navigate") return new Response("", { status: 504 });
    return new Response(OFFLINE_PAGE, { headers: { "Content-Type": "text/html" } });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(request);
  const update = fetch(request).then((response) => {
    if (response.ok || response.type === "opaque") cache.put(request, response.clone());
    return response;
  }).catch(() => null);
  return hit || (await update) || new Response("", { status: 504 });
}

async function cacheFirst(request) {
  const cache = await caches.open(PHOTOS);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;
  if (sameOrigin && url.pathname.startsWith("/api/")) return;
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
  } else if (sameOrigin && (url.pathname.startsWith("/photos/") || url.pathname.startsWith("/uploads/"))) {
    event.respondWith(cacheFirst(request));
  } else if (sameOrigin && url.pathname.startsWith("/static/")) {
    // App files: always fresh when online, so a new page never runs with an old script.
    event.respondWith(networkFirst(request));
  } else if (STATIC_HOSTS.includes(url.hostname)) {
    event.respondWith(staleWhileRevalidate(request));
  }
});

async function sendQueued() {
  const results = await self.CCOffline.flush();
  if (!results.length) return;
  const clients = await self.clients.matchAll({ includeUncontrolled: true });
  clients.forEach((client) => client.postMessage({ type: "cc-sent" }));
  if (!clients.length && self.registration.showNotification) {
    const sent = results.filter((r) => r.ok).length;
    const text = sent ? sent + " report(s) saved offline were sent to the municipal team." : "A report saved offline was not accepted. Open the app to see why.";
    self.registration.showNotification("Clean City", { body: text, icon: "/static/icon-192.png" }).catch(() => null);
  }
}

self.addEventListener("sync", (event) => {
  if (event.tag !== "cc-send") return;
  event.waitUntil(sendQueued().then(() => self.CCOffline.list()).then((left) => {
    // Rejecting makes the browser try again later while reports are still waiting.
    if (left.length) throw new Error("Reports still waiting to be sent");
  }));
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "cc-flush") event.waitUntil(sendQueued());
});
