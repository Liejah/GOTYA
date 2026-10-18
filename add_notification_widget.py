from pathlib import Path
from datetime import datetime
import shutil

files = [
    Path("templates/messages.html"),
    Path("templates/friends.html"),
]

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = Path("notification_backups_" + stamp)
backup_dir.mkdir(exist_ok=False)

style = """
/* GOTYA NOTIFICATION WIDGET */
.gotya-notification-widget{
 position:fixed;right:20px;bottom:20px;z-index:1000;
 font-family:Inter,"Segoe UI",Arial,sans-serif;
}
.gotya-notification-bell{
 width:54px;height:54px;border-radius:50%;
 box-shadow:0 5px 18px #17203330;font-size:21px;
 display:flex;align-items:center;justify-content:center;
 position:relative;
}
.gotya-notification-count{
 position:absolute;right:-3px;top:-4px;
 min-width:21px;height:21px;padding:0 5px;
 border-radius:20px;background:#e53952;color:#fff;
 border:2px solid #fff;font-size:11px;font-weight:800;
 display:flex;align-items:center;justify-content:center;
}
.gotya-notification-panel{
 position:absolute;right:0;bottom:65px;
 width:min(360px,calc(100vw - 28px));max-height:65vh;
 overflow:auto;background:#fff;border:1px solid #e5e9f0;
 border-radius:17px;box-shadow:0 12px 38px #17203325;
 color:#172033;
}
.gotya-notification-head{
 padding:15px;border-bottom:1px solid #e5e9f0;
 display:flex;align-items:center;justify-content:space-between;gap:8px;
}
.gotya-notification-head strong{font-size:17px}
.gotya-notification-enable{font-size:12px;padding:8px 10px}
.gotya-notification-item{
 display:block;padding:13px 15px;border-bottom:1px solid #edf0f5;
 color:inherit;text-decoration:none;overflow-wrap:anywhere;
}
.gotya-notification-item:hover{background:#f4f8ff}
.gotya-notification-item strong{display:block;font-size:14px;margin-bottom:4px}
.gotya-notification-item span{display:block;color:#687386;font-size:13px;line-height:1.4}
.gotya-notification-empty{padding:24px 15px;text-align:center;color:#687386;font-size:14px}
.gotya-notification-status{padding:9px 15px;font-size:12px;color:#687386}
@media(max-width:600px){
 .gotya-notification-widget{right:14px;bottom:14px}
 .gotya-notification-bell{width:50px;height:50px}
 .gotya-notification-panel{bottom:60px;max-height:60vh}
}
"""

markup = """
<div class="gotya-notification-widget" id="gotyaNotificationWidget">
  <section class="gotya-notification-panel" id="gotyaNotificationPanel" hidden>
    <div class="gotya-notification-head">
      <strong>Notifications</strong>
      <button type="button" class="secondary gotya-notification-enable"
              id="gotyaNotificationEnable">Enable browser alerts</button>
    </div>
    <div id="gotyaNotificationList">
      <div class="gotya-notification-empty">Loading notifications…</div>
    </div>
    <div class="gotya-notification-status" id="gotyaNotificationStatus"
         role="status">Browser alerts are optional.</div>
  </section>
  <button type="button" class="gotya-notification-bell"
          id="gotyaNotificationToggle" aria-label="Open notifications"
          aria-expanded="false" aria-controls="gotyaNotificationPanel">
    <span aria-hidden="true">♧</span>
    <span class="gotya-notification-count" id="gotyaNotificationCount" hidden>0</span>
  </button>
</div>
"""

for path in files:
    original = path.read_text(encoding="utf-8")
    if "gotyaNotificationWidget" in original:
        raise SystemExit(f"STOP: {path} already contains the notification widget.")
    if original.count("</style>") != 1 or original.count("</header>") != 1:
        raise SystemExit(f"STOP: Unexpected structure in {path}; no templates edited.")

    shutil.copy2(path, backup_dir / path.name)
    updated = original.replace("</style>", style + "\n</style>", 1)
    updated = updated.replace("</header>", "</header>\n" + markup, 1)
    path.write_text(updated, encoding="utf-8", newline="")
    print("Updated:", path)

print("Backups saved in:", backup_dir)
