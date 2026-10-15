const CACHE_NAME = "gotya-static-v1";
const STATIC_FILES = [
  "/static/mobile.css",
  "/static/gotya-icon.svg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(key => key.startsWith("gotya-static-") && key !== CACHE_NAME)
            .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});


self.addEventListener("push", event => {
  let payload = {};

  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = {
      title: "Gotya",
      body: event.data ? event.data.text() : "You have a new notification."
    };
  }

  const title = typeof payload.title === "string" && payload.title
    ? payload.title
    : "Gotya";

  const options = {
    body: typeof payload.body === "string"
      ? payload.body
      : "You have a new notification.",
    icon: "/static/gotya-icon.svg",
    badge: "/static/gotya-icon.svg",
    data: {
      url: typeof payload.url === "string" &&
           payload.url.startsWith("/") &&
           !payload.url.startsWith("//")
        ? payload.url
        : "/"
    }
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();

  const target = event.notification.data && event.notification.data.url;
  if (!target || typeof target !== "string" || !target.startsWith("/")) return;

  event.waitUntil((async () => {
    const url = new URL(target, self.location.origin);
    if (url.origin !== self.location.origin) return;

    const windows = await self.clients.matchAll({
      type: "window",
      includeUncontrolled: true
    });

    for (const client of windows) {
      if ("focus" in client) {
        await client.navigate(url.href);
        return client.focus();
      }
    }

    if (self.clients.openWindow) {
      return self.clients.openWindow(url.href);
    }
  })());
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const isApprovedStatic =
    url.pathname === "/static/mobile.css" ||
    url.pathname === "/static/gotya-icon.svg";

  if (!isApprovedStatic) return;

  event.respondWith(
    caches.match(request).then(cached =>
      cached || fetch(request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      })
    )
  );
});
