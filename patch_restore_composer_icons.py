from pathlib import Path
from datetime import datetime
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")
backup = p.with_name(
    f"conversation_before_icon_size_fix_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(s, encoding="utf-8")

changes = [
    (
        ".composer-icon{width:30px;height:34px;flex:0 0 30px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--blue);border-radius:50%;-webkit-tap-highlight-color:transparent}",
        ".composer-icon{width:36px;height:40px;flex:0 0 36px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--blue);border-radius:50%;-webkit-tap-highlight-color:transparent}"
    ),
    (
        ".send{width:34px;height:34px;flex:0 0 34px;padding:0;border-radius:50%;display:flex;align-items:center;justify-content:center}",
        ".send{width:40px;height:40px;flex:0 0 40px;padding:0;border-radius:50%;display:flex;align-items:center;justify-content:center}"
    ),
    (
        " .composer-icon{width:28px;flex-basis:28px;height:34px}",
        " .composer-icon{width:32px;flex-basis:32px;height:40px}"
    ),
    (
        " .send{width:32px;height:32px;flex-basis:32px}",
        " .send{width:38px;height:38px;flex-basis:38px}"
    ),
]

for old, new in changes:
    if old not in s:
        print("STOP: Expected icon style not found. No changes saved.")
        print("Missing:", old)
        sys.exit(1)
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
print("SUCCESS: Gallery and send button sizes restored.")
print("The compact typing-box styles were left unchanged.")
print("Backup:", backup)
