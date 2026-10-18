from pathlib import Path
from datetime import datetime
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")

backup = p.with_name(
    f"conversation_before_slim_typing_pill_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(s, encoding="utf-8")

css = """
/* Slim typing pill only; keep gallery and send buttons large */
.composer .composer-input-wrap {
    min-height: 22px !important;
    padding: 0 10px !important;
    border-radius: 18px !important;
}
.composer .composer-input-wrap textarea {
    height: 22px;
    min-height: 22px !important;
    max-height: 58px !important;
    padding: 0 !important;
    line-height: 18px !important;
    font-size: 16px !important;
    box-sizing: border-box !important;
}
@media (max-width: 600px) {
    .composer .composer-input-wrap {
        padding-left: 9px !important;
        padding-right: 9px !important;
    }
}
"""

if "</style>" not in s:
    print("STOP: Could not find the style section. No changes saved.")
    sys.exit(1)

s = s.replace("</style>", css + "\n</style>", 1)

old_js = '''input.addEventListener("input", () => {
  input.style.height = "24px";
  const maxHeight = 64;
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
});'''

new_js = '''input.addEventListener("input", () => {
  input.style.height = "22px";
  const maxHeight = 58;
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
});'''

if old_js not in s:
    print("STOP: Expected resize code not found. No changes saved.")
    sys.exit(1)

s = s.replace(old_js, new_js, 1)
p.write_text(s, encoding="utf-8")

print("SUCCESS: Typing pill slimmed down; icons remain large.")
print("Backup:", backup)
