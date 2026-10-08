// Minimal service worker: lets Android install Clean City as an app and shows a message when offline.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(() => new Response(
      "<h1>Clean City</h1><p>You are offline. Connect to the internet and try again.</p>",
      { headers: { "Content-Type": "text/html" } }
    ))
  );
});
