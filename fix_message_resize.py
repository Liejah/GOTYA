from pathlib import Path
from datetime import datetime
import re
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")

backup = p.with_name(
    f"conversation_before_resize_fix_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(s, encoding="utf-8")

pattern = re.compile(
    r'function resizeMessageBox\(\)\s*\{.*?\n\}',
    re.DOTALL
)

new_function = '''function resizeMessageBox() {
  // Reset first so shrinking and growing both work on Android.
  input.style.height = "auto";

  const maxHeight = 58;
  const requiredHeight = input.scrollHeight;

  input.style.height = Math.min(requiredHeight, maxHeight) + "px";
  input.style.overflowY = requiredHeight > maxHeight ? "auto" : "hidden";
}'''

if not pattern.search(s):
    print("STOP: Resize function not found. No changes saved.")
    sys.exit(1)

s = pattern.sub(new_function, s, count=1)

# Keep the CSS limit consistent with the JavaScript limit.
css = """
/* Final Android textarea sizing */
.composer .composer-input-wrap textarea {
    min-height: 26px !important;
    max-height: 58px !important;
    padding-top: 2px !important;
    padding-bottom: 2px !important;
    line-height: 18px !important;
    box-sizing: border-box !important;
    overflow-x: hidden;
}
"""

if "</style>" not in s:
    print("STOP: Style section not found. No changes saved.")
    sys.exit(1)

s = s.replace("</style>", css + "\n</style>", 1)
p.write_text(s, encoding="utf-8")

print("SUCCESS: Auto-resize now resets before measuring text.")
print("Maximum height: 58px, approximately three lines.")
print("Backup:", backup)
