const CACHE_NAME = "gotya-static-v2";
const PROFILE_META_CACHE = "gotya-profile-meta";
const PROFILE_META_URL = "/__gotya_offline__/active-account";

const STATIC_FILES = [
  "/static/mobile.css",
  "/static/gotya-icon.svg",
  "/static/offline.html"
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
        keys.filter(key =>
          key.startsWith("gotya-static-") && key !== CACHE_NAME
        ).map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

async function getActiveAccount() {
  const cache = await caches.open(PROFILE_META_CACHE);
  const response = await cache.match(PROFILE_META_URL);
  return response ? await response.text() : null;
}

async function setActiveAccount(accountId) {
  if (!/^\d+$/.test(accountId)) return;

  const previous = await getActiveAccount();

  if (previous && previous !== accountId) {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter(key => key.startsWith("gotya-profile-"))
        .map(key => caches.delete(key))
    );
  }

  const cache = await caches.open(PROFILE_META_CACHE);
  await cache.put(
    PROFILE_META_URL,
    new Response(accountId, {
      headers: { "Content-Type": "text/plain" }
    })
  );
}

async function clearProfileCaches() {
  const keys = await caches.keys();
  await Promise.all(
    keys.filter(key => key.startsWith("gotya-profile-"))
      .map(key => caches.delete(key))
  );

  const meta = await caches.open(PROFILE_META_CACHE);
  await meta.delete(PROFILE_META_URL);
}

function isProfilePage(pathname) {
  return pathname === "/profile" || /^\/u\/\d+\/?$/.test(pathname);
}

function isProfileImage(pathname) {
  return pathname.startsWith("/profile-pictures/") ||
         pathname.startsWith("/cover-photos/");
}

function profileCacheRequest(request) {
  const normalized = new URL(request.url);
  normalized.searchParams.delete("offline_account");
  return new Request(normalized.href, request);
}

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

  event.waitUntil(
    self.registration.showNotification(title, {
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
    })
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

    if (self.clients.openWindow) return self.clients.openWindow(url.href);
  })());
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const response = await fetch(request);
        const accountId = response.headers.get("X-Gotya-Account");

        if (accountId && /^\d+$/.test(accountId)) {
          await setActiveAccount(accountId);

          if (isProfilePage(url.pathname) && response.ok) {
            const cache = await caches.open("gotya-profile-" + accountId);
            await cache.put(profileCacheRequest(request), response.clone());
          }
        } else if (
          accountId === "anonymous" &&
          (url.pathname === "/" || url.pathname === "/login")
        ) {
          await clearProfileCaches();
        }

        return response;
      } catch (_) {
        if (isProfilePage(url.pathname)) {
          const accountId = await getActiveAccount();

          if (accountId && /^\d+$/.test(accountId)) {
            const cache = await caches.open("gotya-profile-" + accountId);
            const saved = await cache.match(profileCacheRequest(request));
            if (saved) return saved;
          }
        }

        const cache = await caches.open(CACHE_NAME);
        return await cache.match("/static/offline.html") ||
          new Response("You are offline.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" }
          });
      }
    })());
    return;
  }

  const approvedStatic =
    url.pathname === "/static/mobile.css" ||
    url.pathname === "/static/gotya-icon.svg" ||
    url.pathname === "/static/offline.html";

  if (approvedStatic) {
    event.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        try {
          const response = await fetch(request);
          if (response.ok) await cache.put(request, response.clone());
          return response;
        } catch (_) {
          return await cache.match(request) ||
            new Response("", { status: 503 });
        }
      })
    );
  }
});

