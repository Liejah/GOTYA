from pathlib import Path
from datetime import datetime
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")

backup = p.with_name(
    f"conversation_before_messenger_sizing_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(s, encoding="utf-8")

old_js = '''input.addEventListener("input", () => {
  input.style.height = "22px";
  const maxHeight = 58;
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
});'''

new_js = '''function resizeMessageBox() {
  input.style.height = "26px";
  const maxHeight = 54; // Three lines at 18px each
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}

input.addEventListener("input", resizeMessageBox);'''

if old_js not in s:
    print("STOP: Current resize code was not found. No changes saved.")
    sys.exit(1)

s = s.replace(old_js, new_js, 1)

css = """
/* Messenger-like typing box: comfortable first line, up to three lines */
.composer .composer-input-wrap {
    padding: 3px 10px !important;
    border-radius: 20px !important;
}
.composer .composer-input-wrap textarea {
    height: 26px;
    min-height: 26px !important;
    max-height: 54px !important;
    padding: 4px 1px !important;
    line-height: 18px !important;
    font-size: 16px !important;
    box-sizing: border-box !important;
    overflow-y: hidden;
}
@media (max-width: 600px) {
    .composer .composer-input-wrap {
        padding: 3px 9px !important;
    }
}
"""

if "</style>" not in s:
    print("STOP: Style section not found. No changes saved.")
    sys.exit(1)

s = s.replace("</style>", css + "\n</style>", 1)
p.write_text(s, encoding="utf-8")

print("SUCCESS: Typing box adjusted to grow up to three lines.")
print("Gallery and send button sizes were not changed.")
print("Backup:", backup)
