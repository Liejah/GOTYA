from pathlib import Path
from datetime import datetime
import shutil

messages = Path("templates/messages.html")
friends = Path("templates/friends.html")
worker = Path("static/sw.js")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = Path("notification_backups_clicks_" + stamp)
backup_dir.mkdir(exist_ok=False)

paths = [messages, friends, worker]
for path in paths:
    shutil.copy2(path, backup_dir / path.name)

# Incoming call alerts should open the caller's conversation.
for path in [messages, friends]:
    source = path.read_text(encoding="utf-8")
    old = 'href: "/calls/" + encodeURIComponent(call.id),'
    new = 'href: "/messages/" + encodeURIComponent(call.caller_id),'
    if old not in source:
        raise SystemExit(f"STOP: Expected incoming-call link not found in {path}. Backups saved.")
    path.write_text(source.replace(old, new, 1), encoding="utf-8", newline="")
    print("Fixed incoming-call notification link:", path)

# Add notification-click support without changing existing caching handlers.
source = worker.read_text(encoding="utf-8")
marker = 'self.addEventListener("fetch", event => {'
if source.count(marker) != 1:
    raise SystemExit("STOP: Service worker fetch handler marker is unexpected. Backups saved.")

if 'self.addEventListener("notificationclick"' not in source:
    handler = '''
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

'''
    source = source.replace(marker, handler + marker, 1)
    worker.write_text(source, encoding="utf-8", newline="")
    print("Added service-worker notification click handler.")
else:
    print("Service-worker notification click handler already exists.")

print("Backups saved in:", backup_dir)
