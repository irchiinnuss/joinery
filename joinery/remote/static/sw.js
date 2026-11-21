// Joinery Remote's service worker: the page itself works offline (it then says the Mac is
// asleep), and push notifications arrive (ADR 0093). It never caches project data: only the
// page's own files, and nothing under /api.
const SHELL = "joinery-remote-v1";
const FILES = [
  "/", "/app.css", "/js/app.js", "/js/api.js", "/js/state.js", "/js/view.js",
  "/manifest.webmanifest", "/icon.svg", "/apple-touch-icon.png", "/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== SHELL).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  // The newest page when the Mac answers, the kept one when it doesn't.
  event.respondWith(
    fetch(event.request)
      .then((answer) => {
        if (answer.ok && FILES.includes(url.pathname)) {
          const copy = answer.clone();
          caches.open(SHELL).then((cache) => cache.put(event.request, copy));
        }
        return answer;
      })
      .catch(() => caches.match(event.request, { ignoreSearch: true }).then((kept) => kept || caches.match("/"))),
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = { text: event.data ? event.data.text() : "" };
  }
  const title = data.project ? `Joinery · ${data.project}` : "Joinery";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      // The page open and in front says it itself.
      if (windows.some((client) => client.visibilityState === "visible")) return undefined;
      return self.registration.showNotification(title, {
        body: data.text || "Journeyman needs you.",
        tag: `${data.project_id || ""}:${data.kind || ""}`,
        icon: "/icon-192.png",
        badge: "/icon-192.png",
        data: { url: data.project_id ? `/#p=${data.project_id}` : "/" },
      });
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((client) => new URL(client.url).origin === location.origin);
      if (open) {
        open.navigate(url).catch(() => {});
        return open.focus();
      }
      return self.clients.openWindow(url);
    }),
  );
});
