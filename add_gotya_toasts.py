from pathlib import Path
from datetime import datetime
import shutil

files = [
    Path("templates/friends.html"),
    Path("templates/messages.html"),
]

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

css = r"""
/* GOTYA LIVE TOAST POPUPS */
#gotyaToastContainer {
  position: fixed;
  right: 16px;
  bottom: calc(86px + env(safe-area-inset-bottom, 0px));
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(370px, calc(100vw - 28px));
  pointer-events: none;
  font-family: Inter, "Segoe UI", Arial, sans-serif;
}
.gotya-toast {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px;
  border: 1px solid #e4eaf2;
  border-radius: 15px;
  background: #fff;
  color: #182230;
  box-shadow: 0 10px 35px rgba(15, 23, 42, .20);
  pointer-events: auto;
  animation: gotyaToastIn .22s ease-out;
}
.gotya-toast-link {
  flex: 1;
  min-width: 0;
  color: inherit;
  text-decoration: none;
  overflow-wrap: anywhere;
}
.gotya-toast-link strong {
  display: block;
  margin-bottom: 4px;
  font-size: 14px;
}
.gotya-toast-link span {
  display: block;
  color: #596579;
  font-size: 13px;
  line-height: 1.4;
}
.gotya-toast-dismiss {
  flex: 0 0 auto;
  border: 0;
  border-radius: 8px;
  background: #f0f2f5;
  color: #344054;
  padding: 5px 9px;
  font-size: 18px;
  cursor: pointer;
}
@keyframes gotyaToastIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (max-width: 600px) {
  #gotyaToastContainer {
    right: 10px;
    bottom: calc(76px + env(safe-area-inset-bottom, 0px));
    width: calc(100vw - 20px);
  }
}
"""

toast_markup = '<div id="gotyaToastContainer" aria-live="polite" aria-atomic="false"></div>\n'

toast_function = r"""
  function showToast(item) {
    if (document.hidden) return;
    const container = document.getElementById("gotyaToastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = "gotya-toast";

    const link = document.createElement("a");
    link.className = "gotya-toast-link";
    link.href = item.href;

    const title = document.createElement("strong");
    title.textContent = item.title;

    const detail = document.createElement("span");
    detail.textContent = item.detail || "You have a new notification.";

    link.append(title, detail);

    const dismiss = document.createElement("button");
    dismiss.className = "gotya-toast-dismiss";
    dismiss.type = "button";
    dismiss.setAttribute("aria-label", "Dismiss notification");
    dismiss.textContent = "\u00d7";
    dismiss.addEventListener("click", () => toast.remove());

    toast.append(link, dismiss);
    container.appendChild(toast);

    while (container.children.length > 4) {
      container.firstElementChild.remove();
    }

    window.setTimeout(() => toast.remove(), 7000);
  }

"""

poll_old = '''          if (!previousKeys.has(item.key) && !seen.has(item.key)) {
            browserAlert(item);
          }'''

poll_new = '''          if (!previousKeys.has(item.key) && !seen.has(item.key)) {
            showToast(item);
            browserAlert(item);
          }'''

prepared = []

for path in files:
    if not path.exists():
        raise SystemExit(f"STOP: File not found: {path}")

    text = path.read_text(encoding="utf-8")

    if "function showToast(item)" in text or 'id="gotyaToastContainer"' in text:
        raise SystemExit(f"STOP: A toast patch may already exist in {path}; no files changed.")

    if text.count("function browserAlert(item) {") != 1:
        raise SystemExit(f"STOP: Unexpected browserAlert function count in {path}; no files changed.")

    if text.count(poll_old) != 1:
        raise SystemExit(f"STOP: Expected polling code not found exactly once in {path}; no files changed.")

    if text.count("</style>") < 1 or text.count("</body>") != 1:
        raise SystemExit(f"STOP: Expected HTML insertion points not found in {path}; no files changed.")

    prepared.append((path, text))

# Verify both files before writing either one.
for path, text in prepared:
    backup = path.with_name(path.name + f".backup_toasts_{stamp}")
    shutil.copy2(path, backup)

    text = text.replace("</style>", css + "\n</style>", 1)
    text = text.replace("</body>", toast_markup + "</body>", 1)
    text = text.replace("  function browserAlert(item) {", toast_function + "  function browserAlert(item) {", 1)
    text = text.replace(poll_old, poll_new, 1)

    path.write_text(text, encoding="utf-8", newline="")
    print(f"Updated: {path}")
    print(f"Backup:  {backup}")

print("Done. Only friends.html and messages.html were edited.")
