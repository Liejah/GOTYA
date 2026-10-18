from pathlib import Path
from datetime import datetime
import shutil

files = [
    Path("templates/messages.html"),
    Path("templates/friends.html"),
]
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = Path("notification_backups_live_" + stamp)
backup_dir.mkdir(exist_ok=False)

script = r"""
<script id="gotya-notification-live">
(() => {
  const toggle = document.getElementById("gotyaNotificationToggle");
  const panel = document.getElementById("gotyaNotificationPanel");
  const list = document.getElementById("gotyaNotificationList");
  const countBadge = document.getElementById("gotyaNotificationCount");
  const enableButton = document.getElementById("gotyaNotificationEnable");
  const status = document.getElementById("gotyaNotificationStatus");
  if (!toggle || !panel || !list || !countBadge || !enableButton || !status) return;

  const storageKey = "gotya-notification-seen-v1";
  let initialized = false;
  let previousKeys = new Set();
  let polling = false;

  function readSeen() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "[]");
      return new Set(Array.isArray(parsed) ? parsed : []);
    } catch (_) {
      return new Set();
    }
  }

  function saveSeen(keys) {
    try {
      localStorage.setItem(storageKey, JSON.stringify([...keys].slice(-300)));
    } catch (_) {}
  }

  function addItem(container, title, detail, href, key, kind) {
    const a = document.createElement("a");
    a.className = "gotya-notification-item";
    a.href = href;
    a.dataset.notificationKey = key;

    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = detail || "";
    a.append(strong, span);
    container.appendChild(a);
  }

  function makeNotifications(data) {
    const items = [];
    (data.unread_messages || []).forEach(message => {
      items.push({
        key: "message:" + message.id,
        title: "Message from " + (message.sender_name || "a friend"),
        detail: message.preview || "You received a message.",
        href: "/messages/" + encodeURIComponent(message.sender_id),
        kind: "message"
      });
    });
    (data.pending_friend_requests || []).forEach(request => {
      items.push({
        key: "friend:" + request.id,
        title: "Friend request",
        detail: (request.sender_name || "Someone") + " wants to connect with you.",
        href: "/friends",
        kind: "friend"
      });
    });
    if (data.incoming_call) {
      const call = data.incoming_call;
      items.push({
        key: "call:" + call.id,
        title: "Incoming video call",
        detail: (call.caller_name || "A friend") + " is calling you.",
        href: "/calls/" + encodeURIComponent(call.id),
        kind: "call"
      });
    }
    return items;
  }

  function browserAlert(item) {
    if (!document.hidden || !("Notification" in window) ||
        Notification.permission !== "granted") return;

    const options = {
      body: item.detail,
      icon: "/static/gotya-icon.svg",
      tag: item.key,
      data: { url: item.href }
    };

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.ready
        .then(registration => registration.showNotification(item.title, options))
        .catch(() => {});
    } else {
      try {
        const notification = new Notification(item.title, options);
        notification.onclick = () => {
          window.focus();
          window.location.href = item.href;
        };
      } catch (_) {}
    }
  }

  function updatePermissionText() {
    if (!("Notification" in window)) {
      status.textContent = "Browser notifications are not supported here.";
      enableButton.disabled = true;
      return;
    }
    if (Notification.permission === "granted") {
      status.textContent = "Browser alerts are enabled.";
      enableButton.textContent = "Alerts enabled";
      enableButton.disabled = true;
    } else if (Notification.permission === "denied") {
      status.textContent = "Alerts are blocked by browser settings. Allow them in site settings to enable.";
    } else {
      status.textContent = "Browser alerts are optional. Click Enable browser alerts to allow them.";
    }
  }

  toggle.addEventListener("click", () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
    if (opening) poll();
  });

  enableButton.addEventListener("click", async () => {
    if (!("Notification" in window)) {
      updatePermissionText();
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      updatePermissionText();
      if (permission === "granted") {
        status.textContent = "Browser alerts enabled. Keep this browser running to receive background-tab alerts.";
      }
    } catch (_) {
      status.textContent = "Could not request permission. Check your browser's site settings.";
    }
  });

  list.addEventListener("click", event => {
    const link = event.target.closest("a[data-notification-key]");
    if (!link) return;
    const seen = readSeen();
    seen.add(link.dataset.notificationKey);
    saveSeen(seen);
  });

  async function poll() {
    if (polling || !document.visibilityState) return;
    polling = true;
    try {
      const response = await fetch("/notifications/updates", {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
        cache: "no-store"
      });
      if (!response.ok) throw new Error("Notification request failed");
      const data = await response.json();
      const items = makeNotifications(data);

      const total = Math.max(0, Number(data.unread_count) || 0) +
        (data.pending_friend_requests || []).length +
        (data.incoming_call ? 1 : 0);
      countBadge.textContent = total > 99 ? "99+" : String(total);
      countBadge.hidden = total === 0;
      toggle.setAttribute("aria-label", total ? "Open notifications, " + total + " unread" : "Open notifications");

      list.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "gotya-notification-empty";
        empty.textContent = "You're all caught up.";
        list.appendChild(empty);
      } else {
        items.forEach(item => addItem(list, item.title, item.detail, item.href, item.key, item.kind));
      }

      const currentKeys = new Set(items.map(item => item.key));
      if (initialized) {
        const seen = readSeen();
        items.forEach(item => {
          if (!previousKeys.has(item.key) && !seen.has(item.key)) {
            browserAlert(item);
          }
        });
      } else {
        initialized = true;
      }
      previousKeys = currentKeys;
      updatePermissionText();
    } catch (error) {
      status.textContent = "Couldn't refresh notifications. Check your connection and try again.";
    } finally {
      polling = false;
    }
  }

  updatePermissionText();
  poll();
  window.setInterval(poll, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
})();
</script>
"""

for path in files:
    original = path.read_text(encoding="utf-8")
    if 'id="gotya-notification-live"' in original:
        raise SystemExit(f"STOP: live notification script already exists in {path}.")
    if original.count("</body>") != 1:
        raise SystemExit(f"STOP: Unexpected body closing tag in {path}.")

for path in files:
    shutil.copy2(path, backup_dir / path.name)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("</body>", script + "\n</body>", 1),
                    encoding="utf-8", newline="")
    print("Added live notification polling:", path)

print("Backups saved in:", backup_dir)
